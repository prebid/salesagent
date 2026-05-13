"""Sprint 4 — UI hardening for embedded-mode tenants.

The publisher-facing admin UI hides platform-managed surfaces when
``tenant.is_embedded=True``. See
``docs/design/embedded-mode-sprint-4-ui-hardening.md``.

Covered surfaces:
- ``/tenant/{id}/users`` returns 200 with the lock banner instead of the
  user list (so deep-links from the setup-task panel land on an
  explanation rather than a dead end).
- ``/tenant/{id}/settings`` omits Account → Org Info / Branding / Domain
  Configuration / Access Control sections, the entire Ad Server
  Configuration section, and renders currency read-only (no add/remove
  UI).
- Open-instance tenants on the same deployment continue to render the
  full editable UI — the hide is keyed off ``tenant.is_embedded`` only.
"""

from __future__ import annotations

import uuid

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import CurrencyLimit, Tenant

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Test app + auth fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(integration_db, monkeypatch):
    """Build the full admin Flask app — exercises real route registrations
    and the real Jinja templates."""
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")
    # Make sure embedded-mode header bypass doesn't intervene — these tests
    # exercise the read-path UI surface, not the X-Identity-* contract.
    monkeypatch.delenv("MANAGED_INSTANCE", raising=False)

    from src.admin.app import create_app

    application = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})
    return application


@pytest.fixture
def client(app):
    c = app.test_client()
    # Test super-admin session bypasses the OAuth check — see
    # ``require_tenant_access`` (test_user + super_admin role).
    with c.session_transaction() as sess:
        sess["test_user"] = {"email": "admin@example.com", "name": "Admin"}
        sess["test_user_role"] = "super_admin"
        sess["test_tenant_id"] = "*"
    return c


# ---------------------------------------------------------------------------
# Tenant fixtures
# ---------------------------------------------------------------------------


def _insert_tenant(*, is_embedded: bool, external_source: str | None = None) -> str:
    """Insert a minimal Tenant + a USD CurrencyLimit so the Settings page
    has currency rows to render. Bypasses the model-layer write guard the
    same way ``test_managed_mode_auth_bypass.py`` does."""

    tid = f"t_{'man' if is_embedded else 'open'}_{uuid.uuid4().hex[:8]}"
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant = Tenant(
            tenant_id=tid,
            name="UI Hardening Test",
            subdomain=tid,
            ad_server="mock",
            is_active=True,
            billing_plan="standard",
            authorized_emails=[],
            authorized_domains=[],
            auto_approve_format_ids=[],
            policy_settings={},
            is_embedded=is_embedded,
            external_source=external_source,
            external_org_id=f"org_{uuid.uuid4().hex[:8]}" if is_embedded else None,
        )
        session.add(tenant)
        session.add(CurrencyLimit(tenant_id=tid, currency_code="USD"))
        session.commit()
    return tid


@pytest.fixture
def embedded_tenant_id(integration_db):
    tid = _insert_tenant(is_embedded=True, external_source="scope3")
    yield tid
    _cleanup(tid)


@pytest.fixture
def open_tenant_id(integration_db):
    tid = _insert_tenant(is_embedded=False)
    yield tid
    _cleanup(tid)


def _cleanup(tid: str) -> None:
    from src.core.database.models import AdapterConfig, Principal, PropertyTag

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        for model in (AdapterConfig, CurrencyLimit, PropertyTag, Principal):
            session.execute(model.__table__.delete().where(model.tenant_id == tid))
        session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


# ---------------------------------------------------------------------------
# /tenant/{id}/users
# ---------------------------------------------------------------------------


class TestUsersRouteHiddenOnEmbedded:
    def test_embedded_tenant_returns_200_with_banner(self, client, embedded_tenant_id):
        """Embedded tenant: 200 + lock banner — NOT 404. Deep-links from
        the setup-tasks panel must land on an explanation."""
        resp = client.get(f"/tenant/{embedded_tenant_id}/users")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        assert "Platform settings managed by" in body
        assert "Scope3" in body  # external_source was "scope3" → titled "Scope3"

    def test_embedded_tenant_omits_user_management_form(self, client, embedded_tenant_id):
        """The Add User form must NOT render on embedded tenants."""
        resp = client.get(f"/tenant/{embedded_tenant_id}/users")
        body = resp.get_data(as_text=True)
        # The Add User form posts to /add — confirm the form action is gone.
        assert "/users/add" not in body
        # Domain/email management forms also belong on this page.
        assert 'name="email"' not in body or "Add User" not in body

    def test_open_tenant_renders_user_list(self, client, open_tenant_id):
        """Open-instance tenants are unaffected by the hide."""
        resp = client.get(f"/tenant/{open_tenant_id}/users")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Platform settings managed by" not in body
        # The Add User form action is present on the open-instance page.
        assert "/users/add" in body


# ---------------------------------------------------------------------------
# /tenant/{id}/settings
# ---------------------------------------------------------------------------


class TestSettingsHiddenSectionsOnEmbedded:
    def test_embedded_omits_organization_information(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        # The Org-Info form posts to /settings/general. On embedded tenants
        # this form (and its tenant-name input) must be hidden.
        assert "/settings/general" not in body
        assert 'name="name"' not in body
        # Banner is present on the locked Account section.
        assert "Platform settings managed by" in body

    def test_embedded_omits_branding_section(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # Branding heading and favicon-upload form action.
        assert "<h3>Branding</h3>" not in body
        assert "/upload_favicon" not in body

    def test_embedded_omits_access_control_section(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # Access Control heading + the domain/email POST endpoints.
        assert "Access Control" not in body
        assert "/domains/add" not in body
        assert "/emails/add" not in body

    def test_embedded_omits_ad_server_configuration(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # The Ad Server section has a unique heading and adapter-card markup.
        assert "Ad Server Configuration" not in body
        assert "Available Ad Servers" not in body
        assert "selectGAMAdapter()" not in body

    def test_embedded_omits_settings_nav_tabs(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # Account + Users & Access + Ad Server tabs are hidden in the rail.
        # Look for the data-section attributes that drive the tab links.
        assert 'data-section="account"' not in body
        assert 'data-section="adserver"' not in body
        # Users-tab also drops on embedded.
        assert "Users &amp; Access" not in body and "Users & Access" not in body

    def test_open_tenant_renders_all_sections(self, client, open_tenant_id):
        """Regression guard: every hidden surface still renders for
        open-instance tenants."""
        resp = client.get(f"/tenant/{open_tenant_id}/settings")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "/settings/general" in body
        assert "<h3>Branding</h3>" in body
        assert "Access Control" in body
        assert "Ad Server Configuration" in body
        assert 'data-section="account"' in body
        assert 'data-section="adserver"' in body

    def test_embedded_default_section_is_business_rules(self, client, embedded_tenant_id):
        """Regression: in embedded mode the Account section is hidden as
        a banner stub, so something else must be the initial-render
        ``.active`` section. The shared ``default_section`` template
        variable should ensure exactly one section AND its matching
        nav item carry ``.active``, and they must agree."""
        import re

        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)

        # Exactly one settings-section with .active — JS show/hide has
        # something to display on first render.
        active_sections = re.findall(r'<div id="([^"]+)" class="settings-section active"', body)
        assert len(active_sections) == 1, f"Expected exactly one .active section; got {active_sections}"
        assert active_sections[0] == "business-rules"

        # The matching nav item is also .active and points to the same target.
        active_nav = re.findall(r'<a class="settings-nav-item active" data-section="([^"]+)"', body)
        assert active_nav == ["business-rules"], (
            f"Nav-active and section-active must agree; got nav={active_nav}, section={active_sections}"
        )

    def test_open_default_section_is_account(self, client, open_tenant_id):
        """Mirror of the embedded case for open-instance: Account is the
        default landing both in nav and in section initial-render."""
        import re

        resp = client.get(f"/tenant/{open_tenant_id}/settings")
        body = resp.get_data(as_text=True)

        active_sections = re.findall(r'<div id="([^"]+)" class="settings-section active"', body)
        assert active_sections == ["account"], f"Expected only 'account' section .active; got {active_sections}"

        active_nav = re.findall(r'<a class="settings-nav-item active" data-section="([^"]+)"', body)
        assert active_nav == ["account"], f"Expected only 'account' nav .active; got {active_nav}"


# ---------------------------------------------------------------------------
# Currency lock
# ---------------------------------------------------------------------------


class TestCurrencyLockOnEmbedded:
    def test_embedded_currency_renders_read_only(self, client, embedded_tenant_id):
        """No add/remove inputs — currency is provisioned by the upstream
        platform and surfaces as plain text."""
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # The currency value itself must be visible.
        assert "USD" in body
        # The editable inputs and add-modal trigger must NOT be present.
        assert "showAddCurrencyModal()" not in body
        assert 'name="currency_limits[USD][min_package_budget]"' not in body
        assert 'name="currency_limits[USD][max_daily_package_spend]"' not in body
        # And the modal markup itself (with id="new-currency-code") is gone.
        assert 'id="new-currency-code"' not in body

    def test_open_tenant_renders_editable_currency_ui(self, client, open_tenant_id):
        resp = client.get(f"/tenant/{open_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        assert "showAddCurrencyModal()" in body
        assert 'name="currency_limits[USD][min_package_budget]"' in body
        assert 'id="new-currency-code"' in body


# ---------------------------------------------------------------------------
# Banner partial sanity
# ---------------------------------------------------------------------------


class TestLockBanner:
    def test_banner_uses_external_source_title_case(self, client, integration_db):
        """A tenant flagged ``external_source='scope3'`` should surface as
        "Scope3" in the banner — the partial titlecases the value."""
        tid = _insert_tenant(is_embedded=True, external_source="scope3")
        try:
            resp = client.get(f"/tenant/{tid}/users")
            body = resp.get_data(as_text=True)
            assert "Platform settings managed by Scope3" in body
        finally:
            _cleanup(tid)

    def test_banner_falls_back_when_external_source_is_null(self, client, integration_db):
        """``external_source`` may be NULL on edge-case embedded tenants;
        the partial defaults to ``"your platform"`` (then titlecases)."""
        tid = _insert_tenant(is_embedded=True, external_source=None)
        try:
            resp = client.get(f"/tenant/{tid}/users")
            body = resp.get_data(as_text=True)
            # default('your platform') | title → "Your Platform"
            assert "Platform settings managed by Your Platform" in body
        finally:
            _cleanup(tid)


# ---------------------------------------------------------------------------
# Settings → Publisher Partnerships
# ---------------------------------------------------------------------------


class TestPublisherPartnershipsEditableOnEmbedded:
    """Publisher Partnerships section is editable on embedded tenants.

    Without publishers, embedded tenants cannot create Products (no
    AuthorizedProperty rows means the property selector is empty). The
    PublisherPartner table is not in the model-layer guard's locked set,
    so publishers manage their own partner roster from the embedded UI.
    Closes #336."""

    def test_embedded_renders_publisher_partnerships_section(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        assert "<h2>Publisher Partnerships</h2>" in body
        assert 'data-section="publishers"' in body
        assert "Your agent URL" in body

    def test_embedded_renders_edit_controls(self, client, embedded_tenant_id):
        """Add-Publisher / Refresh-All controls are rendered on embedded."""
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        assert "showAddPublisherModal()" in body
        assert 'id="add-publisher-modal"' in body
        assert "syncAllPublishers()" in body

    def test_open_tenant_renders_publisher_partnerships_with_edit_controls(self, client, open_tenant_id):
        resp = client.get(f"/tenant/{open_tenant_id}/settings")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "<h2>Publisher Partnerships</h2>" in body
        assert 'data-section="publishers"' in body
        assert "showAddPublisherModal()" in body


# ---------------------------------------------------------------------------
# Settings → Danger Zone
# ---------------------------------------------------------------------------


class TestDangerZoneHiddenOnEmbedded:
    """Danger Zone is fully hidden on embedded tenants.

    Scope3 owns tenant lifecycle (provision / deactivate / reactivate via
    Tenant Management API).
    """

    def test_embedded_omits_danger_zone_section(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        # Heading + the deactivate POST endpoint must both be gone.
        assert "Danger Zone" not in body
        assert "/deactivate" not in body
        assert "Deactivate Sales Agent" not in body
        assert 'id="danger-zone"' not in body

    def test_embedded_drops_danger_zone_nav_tab(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        assert 'data-section="danger-zone"' not in body

    def test_open_tenant_renders_danger_zone(self, client, open_tenant_id):
        resp = client.get(f"/tenant/{open_tenant_id}/settings")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Danger Zone" in body
        assert "Deactivate Sales Agent" in body
        assert "/deactivate" in body


# ---------------------------------------------------------------------------
# Settings → Advertisers (read-only directory on embedded)
# ---------------------------------------------------------------------------


class TestAdvertisersDirectoryReadOnlyOnEmbedded:
    """Advertisers directory stays visible on embedded tenants but write
    actions (create / rename / delete) are hidden.

    The "Advertiser" surface in PSA Settings = ``Principal`` (a
    buyer-protocol identity bound to an existing GAM advertiser id).
    Manual creation is hidden in embedded mode because Principal
    provisioning is platform-managed: the upstream platform creates
    Principals via the Tenant Management API (initial_principal on
    /provision, or POST /principals). The directory is a read-only
    view of who's transacting + the resolved GAM advertiser they map to.

    Note: this is NOT about hiding GAM company creation. PSA never
    mints GAM companies for commercial traffic — only for sandbox.
    See docs/design/embedded-mode-sprint-4-ui-hardening.md "Terminology
    pin" for the full distinction.
    """

    def test_embedded_keeps_advertisers_section_and_nav_tab(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        # The directory section heading and nav tab remain.
        assert "<h2>Buyer Agent Management</h2>" in body
        assert 'data-section="advertisers"' in body
        # Read-only note is visible — surfaces the platform-API
        # provisioning rationale so publishers know why there's no
        # Create button.
        assert "provisioned by your platform via the Tenant Management API" in body

    def test_embedded_hides_advertiser_create_button(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # No "Add Buyer Agent" / "Create First Buyer Agent" CTAs, no link to /principals/create.
        assert "Add Buyer Agent" not in body
        assert "Create First Buyer Agent" not in body
        assert "/principals/create" not in body

    def test_embedded_hides_per_row_edit_and_delete_controls(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # Per-row Edit link and Delete button are gone.
        assert "deletePrincipal(" not in body
        assert 'title="Edit Buyer Agent"' not in body
        assert 'title="Delete Buyer Agent"' not in body

    def test_open_tenant_shows_full_advertiser_write_ui(self, client, open_tenant_id):
        resp = client.get(f"/tenant/{open_tenant_id}/settings")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # Section + nav tab are present (regression guard).
        assert "<h2>Buyer Agent Management</h2>" in body
        assert 'data-section="advertisers"' in body
        # Read-only note is NOT shown on open-instance tenants.
        assert "provisioned by your platform via the Tenant Management API" not in body
        # Create CTA is present.
        # When there are 0 advertisers, "Create First Buyer Agent" renders;
        # otherwise the header "Add Buyer Agent" renders. Either is fine —
        # the /principals/create route link is the single canonical signal.
        assert "/principals/create" in body


# ---------------------------------------------------------------------------
# Settings → Advertisers banner pointing standalone publishers to Buyer Routing
# ---------------------------------------------------------------------------


class TestAdvertisersDeprecationBannerOnStandalone:
    """Sprint 5 demoted Settings → Advertisers as the canonical
    advertiser-mapping surface. Standalone tenants now see an in-app
    pointer banner explaining the change + linking to Buyer Routing.

    The banner is purely additive — the directory + Create CTA still
    render below it because Settings → Advertisers is the only place to
    manage buyer-protocol Principal records (access-token-bound buyer
    identities) on standalone tenants.

    Embedded tenants do NOT see the banner: Buyer Routing is the
    canonical surface for them too, but the banner copy talks about
    "managing buyer-protocol Principal records" which is hidden from
    embedded publishers (auto-created from headers). Hence the banner
    sits inside the standalone-only branch.
    """

    def test_standalone_renders_buyer_routing_pointer_banner(self, client, open_tenant_id):
        resp = client.get(f"/tenant/{open_tenant_id}/settings")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        # Headline copy locks the user-visible message.
        assert "Advertiser mapping moved to Buyer Routing" in body
        # The banner's primary CTA links to the canonical Buyer Routing page.
        assert f"/tenant/{open_tenant_id}/buyer-routing" in body

    def test_standalone_keeps_directory_visible(self, client, open_tenant_id):
        """Banner complements rather than replaces the existing section —
        the directory header, Create CTA, and table area must still render
        alongside the new banner."""
        resp = client.get(f"/tenant/{open_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        assert "<h2>Buyer Agent Management</h2>" in body
        assert 'data-section="advertisers"' in body
        # The /principals/create link remains the canonical Create signal.
        assert "/principals/create" in body
        # And the embedded read-only note must NOT appear here.
        assert "auto-created from request headers" not in body

    def test_embedded_does_not_render_banner(self, client, embedded_tenant_id):
        """Regression guard: the banner must sit INSIDE the
        ``{% if not tenant.is_embedded %}`` branch so it never reaches
        embedded publishers (whose copy about "managing buyer-protocol
        Principal records" doesn't apply — those rows are auto-created
        from headers)."""
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        assert "Advertiser mapping moved to Buyer Routing" not in body


# ---------------------------------------------------------------------------
# /tenant/{id}/inventory  (Sync Inventory)
# ---------------------------------------------------------------------------


class TestSyncInventoryHiddenOnEmbedded:
    """The Sync Inventory page is hidden on embedded tenants — the
    deep-link redirects to Browse Inventory (read-only inventory
    surface that embedded publishers DO use).

    Sync itself is driven by the upstream platform via
    ``POST /api/v1/tenant-management/tenants/{id}/refresh``.
    """

    def test_embedded_tenant_redirects_to_browse(self, client, embedded_tenant_id):
        """Embedded `/inventory` redirects to `/inventory/browse` rather
        than landing on a lock banner — the Browse page is the useful
        destination publishers want when they click an inventory deep-link."""
        resp = client.get(f"/tenant/{embedded_tenant_id}/inventory", follow_redirects=False)
        assert resp.status_code in (301, 302, 303, 307, 308)
        assert resp.location.endswith(f"/tenant/{embedded_tenant_id}/inventory/browse")

    def test_embedded_omits_sync_controls(self, client, embedded_tenant_id):
        """After following the redirect, the Browse page renders — and
        critically NOT the sync controls page (which is host-driven)."""
        resp = client.get(f"/tenant/{embedded_tenant_id}/inventory", follow_redirects=True)
        body = resp.get_data(as_text=True)
        # The Sync Inventory page's three sync buttons must NOT render.
        assert "Incremental Sync" not in body
        assert "Full Reset" not in body
        # The targeting-sync button label is unique to the page heading.
        assert 'id="syncTargetingBtn"' not in body

    def test_open_tenant_renders_sync_controls(self, client, open_tenant_id):
        """Open-instance tenants see the narrowed Sync Inventory page —
        sync controls only, no Publishers / Profiles / Browse tabs."""
        resp = client.get(f"/tenant/{open_tenant_id}/inventory")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        # Narrowed page header.
        assert "Sync Inventory" in body
        # The three sync controls are present (mock-adapter tenants see a
        # warning banner instead of buttons, so seed an open GAM tenant
        # in a separate fixture would be required to assert button text;
        # the warning marker is the canonical "page rendered" signal).
        assert "Inventory sync is only available for Google Ad Manager" in body or "Incremental Sync" in body
        # Tabs from the old unified page must not appear.
        assert "Publishers & Properties" not in body
        assert "Inventory Profiles" not in body
        assert "Browse Inventory" not in body or "Browse Inventory</a>" in body

    def test_embedded_dashboard_drops_sync_inventory_link(self, client, embedded_tenant_id):
        """Embedded dashboard nav must NOT link to /tenant/<id>/inventory."""
        resp = client.get(f"/tenant/{embedded_tenant_id}")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        # The Sync Inventory action-button label is unique.
        assert "Sync Inventory" not in body
        # Verify the /inventory route is not linked from the dashboard
        # (other inventory paths like /inventory/browse are allowed).
        assert f'/tenant/{embedded_tenant_id}/inventory"' not in body

    # NOTE: The Ledger dashboard redesign (PR #24) removed top-level inventory
    # nav from the tenant dashboard in favour of the Incoming/Running/Pipeline
    # editorial layout. The "Sync Inventory" link is no longer surfaced from
    # the dashboard — publishers reach Sync Inventory via the Settings rail.
    # The standalone Sync Inventory page (``/tenant/<id>/inventory``) still
    # works (covered by ``test_open_tenant_renders_sync_controls`` above).


# ---------------------------------------------------------------------------
# Top-level inventory nav (Browse / Targeting / Profiles)
# ---------------------------------------------------------------------------


class TestPromotedInventoryNav:
    """Browse Inventory / Targeting Criteria / Inventory Profiles routes
    resolve on both embedded and open-instance tenants.

    The Ledger dashboard redesign (PR #24) replaced the previous
    "promoted top-level nav siblings" with the Incoming/Running/Pipeline
    layout, so the dashboard itself no longer surfaces these as buttons.
    What still must hold: the routes themselves load on both tenant
    flavours, since publishers still need to browse inventory and pick
    targeting when authoring products.
    """

    def test_browse_inventory_resolves_on_open_tenant(self, client, open_tenant_id):
        resp = client.get(f"/tenant/{open_tenant_id}/inventory/browse")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_browse_inventory_resolves_on_embedded_tenant(self, client, embedded_tenant_id):
        """Embedded tenants browse inventory the platform synced for them —
        the page renders normally; only the sync trigger is suppressed
        elsewhere."""
        resp = client.get(f"/tenant/{embedded_tenant_id}/inventory/browse")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_targeting_resolves_on_open_tenant(self, client, open_tenant_id):
        resp = client.get(f"/tenant/{open_tenant_id}/targeting")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_targeting_resolves_on_embedded_tenant(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/targeting")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_inventory_profiles_resolves_on_open_tenant(self, client, open_tenant_id):
        resp = client.get(f"/tenant/{open_tenant_id}/inventory-profiles/")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_inventory_profiles_resolves_on_embedded_tenant(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/inventory-profiles/")
        assert resp.status_code == 200, resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Browse Inventory must not contain Publishers & Properties tab
# ---------------------------------------------------------------------------


class TestPublishersTabRemovedFromBrowseInventory:
    """The "Publishers & Properties" tab was nested under Inventory because
    sync was the entry point. Now Publisher Partnerships lives in Settings
    (and is hidden on embedded tenants — see TestPublisherPartnershipsHiddenOnEmbedded).
    Browse Inventory is just inventory hierarchy + search; no Publisher tab.
    """

    def test_open_browse_inventory_omits_publishers_tab(self, client, open_tenant_id):
        resp = client.get(f"/tenant/{open_tenant_id}/inventory/browse")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        # The unified page used "Publishers & Properties" as a tab label
        # and a section heading; neither belongs on the standalone Browse
        # Inventory page.
        assert "Publishers & Properties" not in body
        assert 'id="publishers-tab"' not in body
        assert 'id="publishers-pane"' not in body
        # The Publisher Partners CTA list is also gone.
        assert "Publisher Partners" not in body or "Manage your publisher partnerships" not in body

    def test_embedded_browse_inventory_omits_publishers_tab(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/inventory/browse")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        assert "Publishers & Properties" not in body
        assert 'id="publishers-tab"' not in body


# ---------------------------------------------------------------------------
# Targeting Criteria — Sync Targeting Data button hidden on embedded
# ---------------------------------------------------------------------------


class TestSyncTargetingDataButtonHiddenOnEmbedded:
    """The Targeting Criteria page stays visible on embedded tenants —
    publishers browse targeting keys when authoring products. Only the
    "Sync Targeting Data" button is suppressed, since sync is driven by
    the upstream platform.
    """

    def test_embedded_omits_sync_targeting_button(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/targeting")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        # The header sync button must be gone.
        assert 'id="sync-targeting-btn"' not in body
        # The page itself must still render — Targeting Criteria heading.
        assert "Targeting Criteria Browser" in body

    def test_open_renders_sync_targeting_button(self, client, open_tenant_id):
        resp = client.get(f"/tenant/{open_tenant_id}/targeting")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        assert 'id="sync-targeting-btn"' in body
        assert "Sync Targeting Data" in body
