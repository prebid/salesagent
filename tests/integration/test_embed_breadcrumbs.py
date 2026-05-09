"""Integration tests for the embed-mode host-link override.

Two surfaces:

- Schema/storage: ``embed_breadcrumb_root`` round-trips through
  provision, GET, and PATCH on the Tenant Management API; bad input
  is rejected by the Pydantic validator (422).
- Rendering: the persistent tenant subnav (in ``base.html``) renders
  the configured override as the leftmost link on embedded tenants,
  ignores it on open-instance tenants, and prefers the
  ``X-Embed-Breadcrumb-Root`` header over the persisted column when
  both are set. The schema field name keeps ``breadcrumb`` in it for
  backwards compatibility with the upstream proxy contract.
"""

from __future__ import annotations

import json
import uuid

import pytest
from flask import Flask

from src.admin.tenant_management_api import tenant_management_api
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    CurrencyLimit,
    Principal,
    PropertyTag,
    Tenant,
)
from tests.helpers.managed_tenant_api import install_management_api_key

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


API_KEY = "sk-embed-breadcrumb-test-key"


# ---------------------------------------------------------------------------
# Tenant Management API fixtures (mirror test_managed_tenant_api.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def install_api_key(integration_db):
    return install_management_api_key(API_KEY)


@pytest.fixture
def app(integration_db, install_api_key):
    application = Flask(__name__)
    application.config["TESTING"] = True
    application.register_blueprint(tenant_management_api)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(install_api_key):
    return {"X-Tenant-Management-API-Key": install_api_key}


@pytest.fixture(autouse=True)
def _stub_adapter_test(monkeypatch):
    """Default adapter probe to success — these tests exercise schema/storage,
    not real adapter wiring."""
    import src.admin.tenant_management_api as api_module

    monkeypatch.setattr(api_module, "test_adapter_connection", lambda *_args, **_kwargs: (True, None))


@pytest.fixture
def cleanup_tenants():
    created: list[str] = []
    yield created
    if not created:
        return
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        for tid in created:
            for model in (AdapterConfig, CurrencyLimit, PropertyTag, Principal):
                session.execute(model.__table__.delete().where(model.tenant_id == tid))
            session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


def _provision_payload(**overrides):
    payload = {
        "name": "Acme News",
        "external_org_id": "org_acme_breadcrumb",
        "external_source": "scope3",
        "contact_email": "ops@example.com",
        "public_agent_url": "https://interchange.io",
        "adapter": {
            "type": "google_ad_manager",
            "network_code": "12345",
            "service_account_email": "sa@example.com",
            "service_account_key_json": '{"type":"service_account"}',
        },
        "default_currency": "USD",
        "billing_plan": "standard",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Storage: provision + patch + GET round-trip
# ---------------------------------------------------------------------------


class TestEmbedBreadcrumbRootStorage:
    def test_provision_persists_breadcrumb_root(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(
            external_org_id="org_breadcrumb_provision",
            embed_breadcrumb_root={"label": "Customer 1000", "url": "https://host.example/customers/1000"},
        )
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201, resp.get_data(as_text=True)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        detail = client.get(f"/api/v1/tenant-management/tenants/{tid}", headers=auth_headers)
        assert detail.status_code == 200
        body = detail.get_json()
        assert body["embed_breadcrumb_root"] == {
            "label": "Customer 1000",
            "url": "https://host.example/customers/1000",
        }

    def test_patch_updates_breadcrumb_root(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_breadcrumb_patch")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"embed_breadcrumb_root": {"label": "Storefront", "url": "https://host.example/store"}},
        )
        assert patch.status_code == 200, patch.get_data(as_text=True)
        body = patch.get_json()
        assert body["embed_breadcrumb_root"] == {
            "label": "Storefront",
            "url": "https://host.example/store",
        }

    def test_patch_omitted_field_preserves_value(self, client, auth_headers, cleanup_tenants):
        """Spec: PATCH with the field absent leaves the existing override
        unchanged (matches public_agent_url semantics)."""
        payload = _provision_payload(
            external_org_id="org_breadcrumb_omit",
            embed_breadcrumb_root={"label": "Original", "url": "https://host.example/orig"},
        )
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        # PATCH some other field — embed_breadcrumb_root must persist.
        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"name": "Renamed"},
        )
        assert patch.status_code == 200, patch.get_data(as_text=True)
        body = patch.get_json()
        assert body["embed_breadcrumb_root"] == {
            "label": "Original",
            "url": "https://host.example/orig",
        }

    def test_patch_rejects_empty_label(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_breadcrumb_empty_label")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"embed_breadcrumb_root": {"label": "", "url": "https://host.example/x"}},
        )
        assert patch.status_code == 422

    def test_patch_rejects_oversized_label(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_breadcrumb_long_label")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"embed_breadcrumb_root": {"label": "x" * 121, "url": "https://host.example/x"}},
        )
        assert patch.status_code == 422

    def test_patch_rejects_non_https_url(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_breadcrumb_non_https")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"embed_breadcrumb_root": {"label": "Storefront", "url": "http://host.example/store"}},
        )
        assert patch.status_code == 422

    def test_patch_accepts_localhost_http_for_dev(self, client, auth_headers, cleanup_tenants):
        """``http://localhost`` and ``http://127.0.0.1`` are accepted so dev
        / Storefront local stacks can wire embed_breadcrumb_root without a
        TLS cert. Loopback only — broader http:// is still rejected."""
        payload = _provision_payload(external_org_id="org_breadcrumb_localhost")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        # http://localhost — accepted
        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"embed_breadcrumb_root": {"label": "Storefront (dev)", "url": "http://localhost:3000/store"}},
        )
        assert patch.status_code == 200, patch.get_data(as_text=True)

        # http://127.0.0.1 — also accepted
        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"embed_breadcrumb_root": {"label": "Storefront (dev)", "url": "http://127.0.0.1:3000/store"}},
        )
        assert patch.status_code == 200

        # http://otherhost — still rejected (loopback exception only)
        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"embed_breadcrumb_root": {"label": "X", "url": "http://otherhost.example/store"}},
        )
        assert patch.status_code == 422

    def test_patch_rejects_extra_keys(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_breadcrumb_extra")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={
                "embed_breadcrumb_root": {
                    "label": "Storefront",
                    "url": "https://host.example/store",
                    "color": "blue",
                }
            },
        )
        assert patch.status_code == 422


# ---------------------------------------------------------------------------
# Rendering: embed-mode breadcrumb partial integration with admin app
# ---------------------------------------------------------------------------


def _insert_render_tenant(*, is_embedded: bool, embed_breadcrumb_root: dict | None) -> str:
    """Insert a minimal Tenant + USD CurrencyLimit so the /users page renders.
    Bypasses the model-layer write guard via management_api_caller marker."""
    tid = f"t_render_{uuid.uuid4().hex[:8]}"
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant = Tenant(
            tenant_id=tid,
            name="Render Test",
            subdomain=tid,
            ad_server="mock",
            is_active=True,
            billing_plan="standard",
            authorized_emails=[],
            authorized_domains=[],
            auto_approve_format_ids=[],
            policy_settings={},
            is_embedded=is_embedded,
            external_source="scope3" if is_embedded else None,
            external_org_id=f"org_{uuid.uuid4().hex[:8]}" if is_embedded else None,
            embed_breadcrumb_root=embed_breadcrumb_root,
        )
        session.add(tenant)
        session.add(CurrencyLimit(tenant_id=tid, currency_code="USD"))
        session.commit()
    return tid


def _cleanup_render_tenant(tid: str) -> None:
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        for model in (AdapterConfig, CurrencyLimit, PropertyTag, Principal):
            session.execute(model.__table__.delete().where(model.tenant_id == tid))
        session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


@pytest.fixture
def admin_app(integration_db, monkeypatch):
    """Build the full admin app for rendering tests."""
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")
    monkeypatch.delenv("MANAGED_INSTANCE", raising=False)

    from src.admin.app import create_app

    application = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})
    return application


@pytest.fixture
def admin_client(admin_app):
    c = admin_app.test_client()
    with c.session_transaction() as sess:
        sess["test_user"] = {"email": "admin@example.com", "name": "Admin"}
        sess["test_user_role"] = "super_admin"
        sess["test_tenant_id"] = "*"
    return c


class TestEmbedBreadcrumbRendering:
    """Embedded host link rendering. Breadcrumbs were dropped (replaced by
    active-state highlighting in the persistent tenant subnav); the host
    label + URL that used to be the first crumb now renders as the
    leftmost item in the subnav, driven by the same ``embed_breadcrumb_root``
    context (header > column precedence)."""

    def test_embedded_tenant_renders_configured_root(self, admin_client):
        """Embedded tenant with a column override: subnav has the host
        link with the configured label + URL."""
        tid = _insert_render_tenant(
            is_embedded=True,
            embed_breadcrumb_root={"label": "Customer 1000", "url": "https://host.example/customers/1000"},
        )
        try:
            resp = admin_client.get(f"/tenant/{tid}/users")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert 'class="sa-tenant-nav"' in body
            assert 'href="https://host.example/customers/1000"' in body
            assert "Customer 1000" in body
        finally:
            _cleanup_render_tenant(tid)

    def test_open_instance_tenant_ignores_column(self, admin_client):
        """Open-instance tenants ignore the override — host link only
        appears inside an embedded deployment."""
        tid = _insert_render_tenant(
            is_embedded=False,
            embed_breadcrumb_root={"label": "Customer 1000", "url": "https://host.example/customers/1000"},
        )
        try:
            resp = admin_client.get(f"/tenant/{tid}/users")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert 'class="sa-tenant-nav"' in body
            assert "Customer 1000" not in body
            assert "host.example" not in body
        finally:
            _cleanup_render_tenant(tid)

    def test_no_override_renders_no_host_link(self, admin_client):
        """Embedded tenant with no override + no header: subnav renders
        without the host link (just the local tenant nav items)."""
        tid = _insert_render_tenant(is_embedded=True, embed_breadcrumb_root=None)
        try:
            resp = admin_client.get(f"/tenant/{tid}/users")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert 'class="sa-tenant-nav"' in body
            assert "host.example" not in body
            assert "sa-nav-action--host" not in body
        finally:
            _cleanup_render_tenant(tid)

    def test_header_overrides_column(self, admin_client):
        """Header takes precedence over the persisted column."""
        tid = _insert_render_tenant(
            is_embedded=True,
            embed_breadcrumb_root={"label": "Column Value", "url": "https://host.example/column"},
        )
        try:
            resp = admin_client.get(
                f"/tenant/{tid}/users",
                headers={
                    "X-Embed-Breadcrumb-Root": json.dumps(
                        {"label": "Header Value", "url": "https://host.example/header"}
                    )
                },
            )
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert "Header Value" in body
            assert 'href="https://host.example/header"' in body
            # Header wins — column value not rendered.
            assert "Column Value" not in body
        finally:
            _cleanup_render_tenant(tid)

    def test_invalid_header_json_falls_through_to_column(self, admin_client):
        """Malformed JSON header: log + ignore, render column value."""
        tid = _insert_render_tenant(
            is_embedded=True,
            embed_breadcrumb_root={"label": "Column Value", "url": "https://host.example/column"},
        )
        try:
            resp = admin_client.get(
                f"/tenant/{tid}/users",
                headers={"X-Embed-Breadcrumb-Root": "not-json{"},
            )
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert "Column Value" in body
            assert 'href="https://host.example/column"' in body
        finally:
            _cleanup_render_tenant(tid)

    def test_invalid_header_shape_falls_through_to_column(self, admin_client):
        """Header missing 'label': falls through to column value."""
        tid = _insert_render_tenant(
            is_embedded=True,
            embed_breadcrumb_root={"label": "Column Value", "url": "https://host.example/column"},
        )
        try:
            resp = admin_client.get(
                f"/tenant/{tid}/users",
                headers={"X-Embed-Breadcrumb-Root": json.dumps({"url": "https://host.example/header"})},
            )
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert "Column Value" in body
        finally:
            _cleanup_render_tenant(tid)


# ---------------------------------------------------------------------------
# Subnav host-link coverage across tenant pages. The persistent subnav
# (rendered by base.html) must render the host link in embedded mode and
# omit it in standalone mode — on every tenant-scoped page, not just ones
# that opted into the old breadcrumb partial.
# ---------------------------------------------------------------------------


HOST_ROOT = {"label": "Storefront", "url": "https://host.example/store"}


def _assert_subnav_has_host_link(body: str) -> None:
    """Embedded mode: subnav renders the host link (label + URL)."""
    assert 'class="sa-tenant-nav"' in body
    assert "sa-nav-action--host" in body
    assert "Storefront" in body
    assert 'href="https://host.example/store"' in body


def _assert_subnav_has_no_host_link(body: str) -> None:
    """Standalone mode: subnav still renders, but without the host link."""
    assert 'class="sa-tenant-nav"' in body
    assert "sa-nav-action--host" not in body
    assert "host.example" not in body


class TestSubnavHostLinkAcrossTenantPages:
    """Every major tenant page must render the subnav with the right
    host-link state — embedded → host link present, standalone → absent.

    Pages without DB scaffolding (products list, media buys list, etc.)
    only need a tenant + USD currency limit; render paths that need
    extra rows fail open with empty lists.
    """

    def test_dashboard_embedded_shows_host_link(self, admin_client):
        tid = _insert_render_tenant(is_embedded=True, embed_breadcrumb_root=HOST_ROOT)
        try:
            resp = admin_client.get(f"/tenant/{tid}/")
            assert resp.status_code == 200
            _assert_subnav_has_host_link(resp.get_data(as_text=True))
        finally:
            _cleanup_render_tenant(tid)

    def test_dashboard_standalone_omits_host_link(self, admin_client):
        tid = _insert_render_tenant(is_embedded=False, embed_breadcrumb_root=None)
        try:
            resp = admin_client.get(f"/tenant/{tid}/")
            assert resp.status_code == 200
            _assert_subnav_has_no_host_link(resp.get_data(as_text=True))
        finally:
            _cleanup_render_tenant(tid)

    def test_products_list_embedded_shows_host_link(self, admin_client):
        tid = _insert_render_tenant(is_embedded=True, embed_breadcrumb_root=HOST_ROOT)
        try:
            resp = admin_client.get(f"/tenant/{tid}/products/")
            assert resp.status_code == 200
            _assert_subnav_has_host_link(resp.get_data(as_text=True))
        finally:
            _cleanup_render_tenant(tid)

    def test_products_list_standalone_omits_host_link(self, admin_client):
        tid = _insert_render_tenant(is_embedded=False, embed_breadcrumb_root=None)
        try:
            resp = admin_client.get(f"/tenant/{tid}/products/")
            assert resp.status_code == 200
            _assert_subnav_has_no_host_link(resp.get_data(as_text=True))
        finally:
            _cleanup_render_tenant(tid)

    def test_media_buys_list_embedded_shows_host_link(self, admin_client):
        tid = _insert_render_tenant(is_embedded=True, embed_breadcrumb_root=HOST_ROOT)
        try:
            resp = admin_client.get(f"/tenant/{tid}/media-buys")
            assert resp.status_code == 200
            _assert_subnav_has_host_link(resp.get_data(as_text=True))
        finally:
            _cleanup_render_tenant(tid)

    def test_media_buys_list_standalone_omits_host_link(self, admin_client):
        tid = _insert_render_tenant(is_embedded=False, embed_breadcrumb_root=None)
        try:
            resp = admin_client.get(f"/tenant/{tid}/media-buys")
            assert resp.status_code == 200
            _assert_subnav_has_no_host_link(resp.get_data(as_text=True))
        finally:
            _cleanup_render_tenant(tid)

    def test_creatives_review_embedded_shows_host_link(self, admin_client):
        """Creatives review used to skip the breadcrumb partial entirely
        — the subnav-in-base.html refactor fixed that gap, and the host
        link must follow."""
        tid = _insert_render_tenant(is_embedded=True, embed_breadcrumb_root=HOST_ROOT)
        try:
            resp = admin_client.get(f"/tenant/{tid}/creatives/review")
            assert resp.status_code == 200
            _assert_subnav_has_host_link(resp.get_data(as_text=True))
        finally:
            _cleanup_render_tenant(tid)

    def test_workflows_embedded_shows_host_link(self, admin_client):
        """Workflows page also missed the old breadcrumb partial."""
        tid = _insert_render_tenant(is_embedded=True, embed_breadcrumb_root=HOST_ROOT)
        try:
            resp = admin_client.get(f"/tenant/{tid}/workflows")
            assert resp.status_code == 200
            _assert_subnav_has_host_link(resp.get_data(as_text=True))
        finally:
            _cleanup_render_tenant(tid)
