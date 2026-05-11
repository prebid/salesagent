"""Integration tests for sprint 2 embedded-mode auth bypass.

When ``MANAGED_INSTANCE=true`` and a tenant is ``is_embedded=True``,
``X-Identity-*`` headers from the upstream proxy authorize the request
without going through the salesagent's Google OAuth flow.

Failure modes match docs/integration/managed-mode-identity-contract.md:

- Managed tenant + missing headers → 403 ``identity_required``
- Managed tenant + ``X-Identity-Org-Id`` doesn't match
  ``tenant.external_org_id`` → 403 ``identity_org_mismatch``
- Managed tenant + valid headers → request passes auth (200/302/etc.,
  whatever the route returns)
- Open-instance tenant on a managed instance → falls through to OAuth
  redirect (today's behavior preserved)
- ``MANAGED_INSTANCE`` unset → bypass disabled, OAuth required for all
  tenants regardless of ``is_embedded`` flag
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant
from tests.helpers.managed_tenant_api import install_management_api_key

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


API_KEY = "sk-embedded-mode-auth-test-key"


@pytest.fixture
def install_api_key(integration_db):
    return install_management_api_key(API_KEY)


@pytest.fixture
def auth_headers(install_api_key):
    return {"X-Tenant-Management-API-Key": install_api_key}


@pytest.fixture
def app(integration_db, install_api_key):
    """Build an app that includes both the management API + the per-tenant
    admin routes (tenants_bp). The bypass lives on tenants_bp's dashboard
    handler via require_tenant_access."""
    from src.admin.app import create_app

    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def managed_tenant(integration_db):
    """Insert a managed tenant directly — bypasses the management API to
    keep the fixture cheap and self-contained."""

    from src.core.database.models import (
        AdapterConfig,
        CurrencyLimit,
        Principal,
        PropertyTag,
    )

    tid = f"t_man_{uuid.uuid4().hex[:8]}"
    org_id = f"org_{uuid.uuid4().hex[:8]}"
    with get_db_session() as session:
        # The model-layer write guard requires ``management_api_caller`` to
        # insert is_embedded=True. Tests bypass the actual API for
        # speed; this flag is the same one the API endpoint sets.
        session.info["management_api_caller"] = True
        session.add(
            Tenant(
                tenant_id=tid,
                name="Managed Auth Test",
                subdomain=tid,
                ad_server="mock",
                is_active=True,
                billing_plan="standard",
                authorized_emails=[],
                authorized_domains=[],
                auto_approve_format_ids=[],
                policy_settings={},
                is_embedded=True,
                external_org_id=org_id,
                external_source="scope3",
            )
        )
        session.commit()
    yield {"tenant_id": tid, "external_org_id": org_id}
    with get_db_session() as session:
        for model in (AdapterConfig, CurrencyLimit, PropertyTag, Principal):
            session.execute(model.__table__.delete().where(model.tenant_id == tid))
        session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


def _identity_headers(org_id: str, *, role: str = "admin") -> dict[str, str]:
    return {
        "X-Identity-Email": "user@scope3.example",
        "X-Identity-Org-Id": org_id,
        "X-Identity-Role": role,
        "X-Identity-Source": "scope3",
        "X-Identity-User-Id": "user-123",
    }


# ---------------------------------------------------------------------------
# MANAGED_INSTANCE=true + is_embedded=True
# ---------------------------------------------------------------------------


class TestManagedModeAuthBypass:
    def test_valid_headers_authorize_dashboard(self, client, managed_tenant, monkeypatch):
        """Valid X-Identity-* + matching org_id → dashboard renders (200)."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.get(
            f"/tenant/{managed_tenant['tenant_id']}",
            headers=_identity_headers(managed_tenant["external_org_id"]),
        )
        # 200 OK (dashboard rendered) or 302 (further internal redirect),
        # but NOT 302 to /login — that would mean auth failed.
        assert resp.status_code in (200, 302), resp.get_data(as_text=True)
        if resp.status_code == 302:
            assert "login" not in (resp.location or ""), f"unexpected redirect to login: {resp.location}"

    def test_missing_headers_returns_403_identity_required(self, client, managed_tenant, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.get(f"/tenant/{managed_tenant['tenant_id']}")
        assert resp.status_code == 403
        body = resp.get_data(as_text=True)
        assert "identity_required" in body, body

    def test_org_id_mismatch_returns_403(self, client, managed_tenant, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.get(
            f"/tenant/{managed_tenant['tenant_id']}",
            headers=_identity_headers("wrong_org_id"),
        )
        assert resp.status_code == 403
        body = resp.get_data(as_text=True)
        assert "identity_org_mismatch" in body, body

    def test_invalid_role_returns_403_identity_required(self, client, managed_tenant, monkeypatch):
        """X-Identity-Role outside admin|member|viewer enum → 403.

        The reader raises InvalidPropagatedIdentity which the bypass
        translates to identity_required (header set is malformed).
        """
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.get(
            f"/tenant/{managed_tenant['tenant_id']}",
            headers=_identity_headers(managed_tenant["external_org_id"], role="superuser"),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Bypass is opt-in — environment toggles
# ---------------------------------------------------------------------------


class TestBypassIsOptIn:
    def test_managed_instance_unset_falls_through_to_oauth(self, client, managed_tenant, monkeypatch):
        """Without MANAGED_INSTANCE=true, X-Identity-* headers are ignored
        and the request hits the normal OAuth gate (302 to /login)."""
        monkeypatch.delenv("MANAGED_INSTANCE", raising=False)
        # Disable test mode too so we don't accidentally pass via test_user
        monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "false")
        resp = client.get(
            f"/tenant/{managed_tenant['tenant_id']}",
            headers=_identity_headers(managed_tenant["external_org_id"]),
        )
        assert resp.status_code == 302
        assert "login" in (resp.location or "")

    def test_open_instance_tenant_on_managed_deployment_uses_oauth(self, client, integration_db, monkeypatch):
        """Tenant without external_org_id on a MANAGED_INSTANCE=true
        deployment falls through to OAuth — embedded mode is unavailable
        when there's no org id to match against."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "false")

        tid = f"t_open_{uuid.uuid4().hex[:8]}"
        with get_db_session() as session:
            session.add(
                Tenant(
                    tenant_id=tid,
                    name="Open Instance Tenant",
                    subdomain=tid,
                    ad_server="mock",
                    is_active=True,
                    billing_plan="standard",
                    authorized_emails=[],
                    authorized_domains=[],
                    auto_approve_format_ids=[],
                    policy_settings={},
                    is_embedded=False,
                )
            )
            session.commit()

        try:
            resp = client.get(
                f"/tenant/{tid}",
                headers=_identity_headers("any_org_id"),
            )
            assert resp.status_code == 302
            assert "login" in (resp.location or "")
        finally:
            with get_db_session() as session:
                session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
                session.commit()


# ---------------------------------------------------------------------------
# Embedded preview: is_embedded=False + external_org_id set
# ---------------------------------------------------------------------------


@pytest.fixture
def preview_tenant(integration_db):
    """Non-embedded tenant with external_org_id set — supports embedded
    auth as a per-request opt-in while still serving OAuth users."""
    from src.core.database.models import (
        AdapterConfig,
        CurrencyLimit,
        Principal,
        PropertyTag,
    )
    from tests.factories import TenantFactory
    from tests.helpers.managed_tenant_api import bind_factories_to_session

    tid = f"t_prev_{uuid.uuid4().hex[:8]}"
    org_id = f"org_{uuid.uuid4().hex[:8]}"
    # bind_factories_to_session binds every factory in ALL_FACTORIES so the
    # RelatedFactory(CurrencyLimitFactory) cascade on TenantFactory has a
    # session too — binding only TenantFactory left the cascaded child
    # without a session and raised "No session provided." on every test
    # in this fixture's class.
    with bind_factories_to_session():
        TenantFactory(
            tenant_id=tid,
            name="Preview Tenant",
            subdomain=tid,
            is_embedded=False,
            external_org_id=org_id,
            external_source="scope3",
        )
    yield {"tenant_id": tid, "external_org_id": org_id}
    with get_db_session() as session:
        for model in (AdapterConfig, CurrencyLimit, PropertyTag, Principal):
            session.execute(model.__table__.delete().where(model.tenant_id == tid))
        session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


class TestEmbeddedPreviewOnNonEmbeddedTenant:
    """is_embedded=False + external_org_id set → embedded auth is *available*
    per-request. Sending matching headers picks embedded mode; absent
    headers fall through to OAuth."""

    def test_matching_headers_authorize_embedded_preview(self, client, preview_tenant, monkeypatch):
        """Caller opts into embedded mode by sending valid X-Identity-* headers
        whose org matches the tenant's external_org_id."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.get(
            f"/tenant/{preview_tenant['tenant_id']}",
            headers=_identity_headers(preview_tenant["external_org_id"]),
        )
        assert resp.status_code in (200, 302), resp.get_data(as_text=True)
        if resp.status_code == 302:
            assert "login" not in (resp.location or ""), f"unexpected redirect to login: {resp.location}"

    def test_no_headers_falls_through_to_oauth(self, client, preview_tenant, monkeypatch):
        """Same tenant, no headers → OAuth path runs (production behavior
        preserved while embedded preview is opt-in)."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "false")
        resp = client.get(f"/tenant/{preview_tenant['tenant_id']}")
        assert resp.status_code == 302
        assert "login" in (resp.location or "")

    def test_org_mismatch_returns_403(self, client, preview_tenant, monkeypatch):
        """Caller sent headers but the org doesn't match — reject rather
        than silently fall through to OAuth (would mask the misuse)."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.get(
            f"/tenant/{preview_tenant['tenant_id']}",
            headers=_identity_headers("wrong_org_id"),
        )
        assert resp.status_code == 403
        assert "identity_org_mismatch" in resp.get_data(as_text=True)

    def test_malformed_headers_return_403(self, client, preview_tenant, monkeypatch):
        """Headers present but malformed (bad role) → 403, never silent
        OAuth fallback."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.get(
            f"/tenant/{preview_tenant['tenant_id']}",
            headers=_identity_headers(preview_tenant["external_org_id"], role="superuser"),
        )
        assert resp.status_code == 403


class TestEmbeddedViewBlocksMutations:
    """Mutation methods (POST/PUT/DELETE/PATCH) on tenant-scoped routes
    must reject header-auth callers when the request is in embedded view —
    preview OR permanently-embedded. The gate lives in
    ``require_tenant_access`` (``_maybe_block_embedded_write``) so every
    blueprint inherits it without per-route bookkeeping. GETs are not
    affected (they render the lock-banner UI normally).

    Closes a pre-existing security gap: lock banners on GET pages did
    nothing to stop a header-auth caller from POSTing directly to mutation
    routes. The decorator now blocks all writes structurally.
    """

    def test_preview_blocks_add_user(self, client, preview_tenant, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.post(
            f"/tenant/{preview_tenant['tenant_id']}/users/add",
            data={"email": "intruder@evil.example", "role": "admin"},
            headers=_identity_headers(preview_tenant["external_org_id"]),
        )
        assert resp.status_code == 403
        assert b"platform-managed" in resp.data

    def test_preview_blocks_enable_setup_mode(self, client, preview_tenant, monkeypatch):
        """Re-enabling setup mode would re-arm the test-credentials backdoor.
        Header-auth callers must not be able to flip it."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.post(
            f"/tenant/{preview_tenant['tenant_id']}/users/enable-setup-mode",
            headers=_identity_headers(preview_tenant["external_org_id"]),
        )
        assert resp.status_code == 403

    def test_preview_blocks_add_domain(self, client, preview_tenant, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.post(
            f"/tenant/{preview_tenant['tenant_id']}/users/domains",
            json={"domain": "evil.example"},
            headers=_identity_headers(preview_tenant["external_org_id"]),
        )
        assert resp.status_code == 403

    def test_managed_blocks_add_user(self, client, managed_tenant, monkeypatch):
        """Same gate fires on permanently-embedded tenants."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.post(
            f"/tenant/{managed_tenant['tenant_id']}/users/add",
            data={"email": "intruder@evil.example", "role": "admin"},
            headers=_identity_headers(managed_tenant["external_org_id"]),
        )
        assert resp.status_code == 403

    def test_preview_blocks_settings_mutation(self, client, preview_tenant, monkeypatch):
        """The decorator gates ALL blueprints, not just users. Any mutation
        route under tenant scope inherits the block — settings, principals,
        currencies, etc. were previously exposed by chrome-only hiding.

        Picks /users/<id>/toggle as a smoke check for a non-add mutation
        method on an arbitrary user_id. The handler never runs (decorator
        intercepts) so the user_id need not exist.
        """
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.post(
            f"/tenant/{preview_tenant['tenant_id']}/users/nonexistent_user/toggle",
            headers=_identity_headers(preview_tenant["external_org_id"]),
        )
        assert resp.status_code == 403
        # Decorator intercepts before the route handler so the response
        # reflects the central gate, not the route's own "user not found".
        assert b"platform-managed" in resp.data

    def test_preview_allows_get_dashboard(self, client, preview_tenant, monkeypatch):
        """GETs are not gated — the chrome shows the lock banner instead.
        Confirms the gate is method-scoped, not blanket-blocking embedded
        views entirely (which would break read access)."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.get(
            f"/tenant/{preview_tenant['tenant_id']}/users",
            headers=_identity_headers(preview_tenant["external_org_id"]),
        )
        assert resp.status_code in (200, 302), resp.get_data(as_text=True)

    def test_api_mode_returns_json_envelope(self, client, preview_tenant, monkeypatch):
        """Routes decorated with ``require_tenant_access(api_mode=True)``
        return JSON, not HTML, on the 403. Stable error code lets
        programmatic callers branch without substring-matching messages.

        ``/publisher-partners`` POST is api_mode=True and stays
        platform-managed in embedded mode (Scope3 owns the partner
        roster — see ``publisher_partners._reject_if_embedded``). Verify
        the JSON envelope shape so a client regression to HTML-only
        responses (or a different code) is caught here.
        """
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.post(
            f"/tenant/{preview_tenant['tenant_id']}/publisher-partners",
            json={"publisher_domain": "evil.example"},
            headers=_identity_headers(preview_tenant["external_org_id"]),
        )
        assert resp.status_code == 403
        body = resp.get_json()
        assert body is not None, f"expected JSON, got: {resp.get_data(as_text=True)[:200]}"
        assert body.get("error") == "embedded_writes_not_permitted"
        assert "platform-managed" in body.get("message", "")


class TestEmbeddedViewAllowsPublisherManagedWrites:
    """Routes that opt in with ``allow_embedded_writes=True`` must pass the
    decorator-level gate so publisher-managed surfaces (buyer routing, products,
    principals, creatives, ...) stay editable from the embedded admin UI. The
    model-layer guard (``src/core/database/embedded_tenant_guard.py``) remains
    the authoritative protection for platform-managed columns regardless.

    Closes salesagent#337 (default-advertiser save was 403 in embedded mode).
    """

    def test_managed_tenant_can_save_default_advertiser(self, client, managed_tenant, monkeypatch):
        """PATCH /buyer-routing/api/default-advertiser is publisher-managed —
        the sprint-5 design requires the publisher (via the embedded iframe)
        to be able to pick a default advertiser. The endpoint sets
        ``management_api_caller=True`` to bypass the model-layer guard on
        ``Tenant.default_gam_advertiser_id``; the decorator must not 403
        the request before it can run."""
        from tests.factories import GamAdvertiserFactory
        from tests.helpers.managed_tenant_api import bind_factories_to_session

        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        tid = managed_tenant["tenant_id"]
        with bind_factories_to_session():
            GamAdvertiserFactory(tenant_id=tid, advertiser_id="12345", name="Test Advertiser")

        resp = client.patch(
            f"/tenant/{tid}/buyer-routing/api/default-advertiser",
            json={"default_gam_advertiser_id": "12345"},
            headers=_identity_headers(managed_tenant["external_org_id"]),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json() == {"default_gam_advertiser_id": "12345"}

    def test_managed_tenant_can_create_routing_rule(self, client, managed_tenant, monkeypatch):
        """POST /buyer-routing/api/rules writes AdvertiserRoutingRule —
        pure publisher-managed table (not in the embedded_tenant_guard's
        locked set). Proves the opt-in works without any model-layer
        bypass flag."""
        from tests.factories import GamAdvertiserFactory
        from tests.helpers.managed_tenant_api import bind_factories_to_session

        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        tid = managed_tenant["tenant_id"]
        with bind_factories_to_session():
            GamAdvertiserFactory(tenant_id=tid, advertiser_id="98765", name="Rule Target")

        resp = client.post(
            f"/tenant/{tid}/buyer-routing/api/rules",
            json={"operator_domain": "wpp.com", "gam_advertiser_id": "98765"},
            headers=_identity_headers(managed_tenant["external_org_id"]),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["operator_domain"] == "wpp.com"
        assert body["gam_advertiser_id"] == "98765"

    def test_managed_tenant_can_create_principal(self, client, managed_tenant, monkeypatch):
        """POST /principals/create writes Principal — publisher-managed per
        embedded_tenant_guard docstring. The publisher (via the iframe)
        provisions buyer agents for their tenant."""
        from src.core.database.models import Principal

        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        tid = managed_tenant["tenant_id"]
        # Form fields match src/admin/blueprints/principals.py:216-236 —
        # ``enable_mock`` synthesizes a mock platform mapping so the
        # at-least-one-platform validator passes.
        resp = client.post(
            f"/tenant/{tid}/principals/create",
            data={"name": "Test Buyer", "enable_mock": "on"},
            headers=_identity_headers(managed_tenant["external_org_id"]),
            follow_redirects=False,
        )
        # Success path is a redirect to the principals list (302).
        # If the gate had still been in place we'd get 403 here.
        assert resp.status_code in (200, 302), resp.get_data(as_text=True)
        with get_db_session() as session:
            created = session.scalars(select(Principal).filter_by(tenant_id=tid, name="Test Buyer")).first()
            assert created is not None, "Principal row was not persisted"


class TestEmbeddedGatePolarityNotInverted:
    """Defensive: a future PR could accidentally land an
    ``allow_embedded_writes=True`` on a platform-managed route (users,
    settings, adapters, signing keys, …). These tests catch that by
    spot-checking that the most sensitive routes still 403 in embedded
    mode.

    They duplicate intent already covered by ``TestEmbeddedViewBlocksMutations``
    but live here so the contract that *some* routes stay gated reads
    next to the contract that *some* are opted in.
    """

    def test_users_add_still_blocked(self, client, preview_tenant, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.post(
            f"/tenant/{preview_tenant['tenant_id']}/users/add",
            data={"email": "intruder@evil.example", "role": "admin"},
            headers=_identity_headers(preview_tenant["external_org_id"]),
        )
        assert resp.status_code == 403
        assert b"platform-managed" in resp.data

    def test_publisher_partners_add_still_blocked(self, client, preview_tenant, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.post(
            f"/tenant/{preview_tenant['tenant_id']}/publisher-partners",
            json={"publisher_domain": "evil.example"},
            headers=_identity_headers(preview_tenant["external_org_id"]),
        )
        assert resp.status_code == 403
