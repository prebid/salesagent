"""Sprint 4 — UI hardening for embedded-mode tenants.

The publisher-facing admin UI hides platform-managed surfaces when
``tenant.is_embedded=True``.

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

import pytest

from tests.integration._embedded_helpers import (
    cleanup_embedded_test_tenant,
    insert_embedded_test_tenant,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Tenant fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def embedded_tenant_id(integration_db):
    tid = insert_embedded_test_tenant(is_embedded=True, external_source="scope3")
    yield tid
    cleanup_embedded_test_tenant(tid)


@pytest.fixture
def open_tenant_id(integration_db):
    tid = insert_embedded_test_tenant(is_embedded=False)
    yield tid
    cleanup_embedded_test_tenant(tid)


# ---------------------------------------------------------------------------
# /tenant/{id}/users
# ---------------------------------------------------------------------------


class TestUsersRouteHiddenOnEmbedded:
    def test_embedded_tenant_returns_200_with_banner(self, embedded_client, embedded_tenant_id):
        """Embedded tenant: 200 + lock banner — NOT 404. Deep-links from
        the setup-tasks panel must land on an explanation."""
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/users")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        assert "Platform settings managed by" in body
        assert "Scope3" in body  # external_source was "scope3" → titled "Scope3"

    def test_embedded_tenant_omits_user_management_form(self, embedded_client, embedded_tenant_id):
        """The Add User form must NOT render on embedded tenants."""
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/users")
        body = resp.get_data(as_text=True)
        # The Add User form posts to /add — confirm the form action is gone.
        assert "/users/add" not in body
        # Domain/email management forms also belong on this page.
        assert 'name="email"' not in body or "Add User" not in body

    def test_open_tenant_renders_user_list(self, embedded_client, open_tenant_id):
        """Open-instance tenants are unaffected by the hide."""
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/users")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Platform settings managed by" not in body
        # The Add User form action is present on the open-instance page.
        assert "/users/add" in body


# ---------------------------------------------------------------------------
# /tenant/{id}/settings
# ---------------------------------------------------------------------------


class TestSettingsHiddenSectionsOnEmbedded:
    def test_embedded_omits_organization_information(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        # The Org-Info form posts to /settings/general. On embedded tenants
        # this form (and its tenant-name input) must be hidden.
        assert "/settings/general" not in body
        assert 'name="name"' not in body
        # Banner is present on the locked Account section.
        assert "Platform settings managed by" in body

    def test_embedded_omits_branding_section(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # Branding heading and favicon-upload form action.
        assert "<h3>Branding</h3>" not in body
        assert "/upload_favicon" not in body

    def test_embedded_omits_access_control_section(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # Access Control heading + the domain/email POST endpoints.
        assert "Access Control" not in body
        assert "/domains/add" not in body
        assert "/emails/add" not in body

    def test_embedded_omits_ad_server_configuration(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # The Ad Server section has a unique heading and adapter-card markup.
        assert "Ad Server Configuration" not in body
        assert "Available Ad Servers" not in body
        assert "selectGAMAdapter()" not in body

    def test_embedded_omits_settings_nav_tabs(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # Account + Users & Access + Ad Server tabs are hidden in the rail.
        # Look for the data-section attributes that drive the tab links.
        assert 'data-section="account"' not in body
        assert 'data-section="adserver"' not in body
        # Users-tab also drops on embedded.
        assert "Users &amp; Access" not in body and "Users & Access" not in body

    def test_open_tenant_renders_all_sections(self, embedded_client, open_tenant_id):
        """Regression guard: every hidden surface still renders for
        open-instance tenants."""
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "/settings/general" in body
        assert "<h3>Branding</h3>" in body
        assert "Access Control" in body
        assert "Ad Server Configuration" in body
        assert 'data-section="account"' in body
        assert 'data-section="adserver"' in body

    def test_embedded_settings_route_renders_locked_page(self, embedded_client, embedded_tenant_id):
        """Sprint 7 Phase 4d: Tenant Settings is collapsed on embedded.
        Every subsection is either promoted out (Phase 2), hard-hidden,
        or platform-managed, so the legacy multi-section page serves no
        purpose. The route now renders ``_embedded_locked_page.html``
        instead — a single 'Platform settings managed by …' banner."""
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # Locked-page wrapper (template title block + banner partial).
        assert "Managed by Platform" in body
        assert "embedded-lock-banner" in body
        assert "Platform settings managed by" in body
        # Legacy multi-section layout is gone.
        assert 'class="settings-layout"' not in body
        assert 'class="settings-section active"' not in body

    def test_open_default_section_is_account(self, embedded_client, open_tenant_id):
        """Mirror of the embedded case for open-instance: Account is the
        default landing both in nav and in section initial-render."""
        import re

        resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings")
        body = resp.get_data(as_text=True)

        active_sections = re.findall(r'<div id="([^"]+)" class="settings-section active"', body)
        assert active_sections == ["account"], f"Expected only 'account' section .active; got {active_sections}"

        active_nav = re.findall(r'<a class="settings-nav-item active" data-section="([^"]+)"', body)
        assert active_nav == ["account"], f"Expected only 'account' nav .active; got {active_nav}"

    def test_embedded_configure_menu_omits_tenant_settings_link(self, embedded_client, embedded_tenant_id):
        """Sprint 7 Phase 4d: the Tenant Settings entry in the global
        Configure → Workspace menu is hidden on embedded. The promoted
        Workspace peer pages (Publishers, Policies & Workflows, Integrations)
        cover everything embedded operators can edit."""
        # Any embedded page renders the global nav — use the locked Tenant
        # Settings page itself for a self-contained probe.
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # The menu link text + href together — guard against the link being
        # rebadged but still pointed at /settings.
        assert ">Tenant Settings</a>" not in body

    def test_open_configure_menu_includes_tenant_settings_link(self, embedded_client, open_tenant_id):
        """Open-instance regression guard: Tenant Settings entry still
        renders in the Workspace submenu."""
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        assert ">Tenant Settings</a>" in body


# ---------------------------------------------------------------------------
# Currency lock
# ---------------------------------------------------------------------------


class TestCurrencyLockOnEmbedded:
    """Sprint 7 Phase 2: currency limits UI moved to the new standalone
    Policies & Workflows page (``/tenant/<id>/settings/policies/``).
    Tests now probe that URL."""

    def test_embedded_currency_renders_read_only(self, embedded_client, embedded_tenant_id):
        """No add/remove inputs — currency is provisioned by the upstream
        platform and surfaces as plain text."""
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings/policies/")
        body = resp.get_data(as_text=True)
        # The currency value itself must be visible.
        assert "USD" in body
        # The editable inputs and add-modal trigger must NOT be present.
        assert "showAddCurrencyModal()" not in body
        assert 'name="currency_limits[USD][min_package_budget]"' not in body
        assert 'name="currency_limits[USD][max_daily_package_spend]"' not in body
        # And the modal markup itself (with id="new-currency-code") is gone.
        assert 'id="new-currency-code"' not in body

    def test_open_tenant_renders_editable_currency_ui(self, embedded_client, open_tenant_id):
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings/policies/")
        body = resp.get_data(as_text=True)
        assert "showAddCurrencyModal()" in body
        assert 'name="currency_limits[USD][min_package_budget]"' in body
        assert 'id="new-currency-code"' in body


# ---------------------------------------------------------------------------
# Banner partial sanity
# ---------------------------------------------------------------------------


class TestLockBanner:
    def test_banner_uses_external_source_title_case(self, embedded_client, integration_db):
        """A tenant flagged ``external_source='scope3'`` should surface as
        "Scope3" in the banner — the partial titlecases the value."""
        tid = insert_embedded_test_tenant(is_embedded=True, external_source="scope3")
        try:
            resp = embedded_client.get(f"/tenant/{tid}/users")
            body = resp.get_data(as_text=True)
            assert "Platform settings managed by Scope3" in body
        finally:
            cleanup_embedded_test_tenant(tid)

    def test_banner_falls_back_when_external_source_is_null(self, embedded_client, integration_db):
        """``external_source`` may be NULL on edge-case embedded tenants;
        the partial defaults to ``"your platform"`` (then titlecases)."""
        tid = insert_embedded_test_tenant(is_embedded=True, external_source=None)
        try:
            resp = embedded_client.get(f"/tenant/{tid}/users")
            body = resp.get_data(as_text=True)
            # default('your platform') | title → "Your Platform"
            assert "Platform settings managed by Your Platform" in body
        finally:
            cleanup_embedded_test_tenant(tid)


# ---------------------------------------------------------------------------
# Settings → Publisher Partnerships
# ---------------------------------------------------------------------------


class TestPublisherPartnershipsEditableOnEmbedded:
    """Publisher Partnerships standalone page is editable on embedded tenants.

    Without publishers, embedded tenants cannot create Products (no
    AuthorizedProperty rows means the property selector is empty). The
    PublisherPartner table is not in the model-layer guard's locked set,
    so publishers manage their own partner roster from the embedded UI.
    Closes #336.

    Sprint 7 Phase 2: the in-page Settings section was promoted to a
    standalone Configure → Workspace peer page at
    ``/tenant/<id>/publishers/``; these tests track the new URL."""

    def test_embedded_renders_publisher_partnerships_page(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/publishers/")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        assert "<h2>Publisher partnerships</h2>" in body
        assert "Your agent URL" in body

    def test_agent_url_copy_control_passes_button_for_feedback(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/publishers/")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'id="public-agent-url-display"' in body
        assert 'onclick="copyAgentUrlToClipboard(this)"' in body

    def test_embedded_renders_edit_controls(self, embedded_client, embedded_tenant_id):
        """Add-Publisher / Refresh-All controls are rendered on embedded."""
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/publishers/")
        body = resp.get_data(as_text=True)
        assert "showAddPublisherModal()" in body
        assert 'id="add-publisher-modal"' in body
        assert "syncAllPublishers()" in body

    def test_open_tenant_renders_publisher_partnerships_with_edit_controls(self, embedded_client, open_tenant_id):
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/publishers/")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "<h2>Publisher partnerships</h2>" in body
        assert "showAddPublisherModal()" in body

    def test_settings_page_no_longer_renders_publishers_section(self, embedded_client, embedded_tenant_id):
        """The in-page Settings section is gone — the tab data-attribute
        and the section's H2 must NOT render anywhere in Tenant Settings."""
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'data-section="publishers"' not in body
        assert "<h2>Publisher Partnerships</h2>" not in body


# ---------------------------------------------------------------------------
# Settings → Danger Zone
# ---------------------------------------------------------------------------


class TestDangerZoneHiddenOnEmbedded:
    """Danger Zone is fully hidden on embedded tenants.

    Scope3 owns tenant lifecycle (provision / deactivate / reactivate via
    Tenant Management API).
    """

    def test_embedded_omits_danger_zone_section(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        # Heading + the deactivate POST endpoint must both be gone.
        assert "Danger Zone" not in body
        assert "/deactivate" not in body
        assert "Deactivate Sales Agent" not in body
        assert 'id="danger-zone"' not in body

    def test_embedded_drops_danger_zone_nav_tab(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        assert 'data-section="danger-zone"' not in body

    def test_open_tenant_renders_danger_zone(self, embedded_client, open_tenant_id):
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Danger Zone" in body
        assert "Deactivate Sales Agent" in body
        assert "/deactivate" in body


# ---------------------------------------------------------------------------
# Settings → Buyer Agents (Sprint 7: hidden entirely on embedded)
# ---------------------------------------------------------------------------


class TestAdvertisersDirectoryHiddenOnEmbedded:
    """Buyer Agents settings tab + section are hidden entirely on embedded tenants.

    Sprint 7 IA cleanup hides the Buyer Agents directory entirely on
    embedded tenants. Buyer Routing
    (Configure → Buying → Buyer routing) is the single canonical home
    for advertiser→buyer-agent mappings on embedded tenants, and
    Principal provisioning is platform-managed via the Tenant
    Management API — there's nothing operator-facing left to show
    under Settings → Buyer Agents, so the whole tab is gated on
    ``not embedded_view``.

    Standalone tenants still see the section with full write actions
    (covered by ``test_open_tenant_shows_full_advertiser_write_ui``
    below).
    """

    def test_embedded_hides_advertisers_section_and_nav_tab(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        # Both the directory section heading and the sidebar nav tab are gone.
        assert "<h2>Buyer Agent Management</h2>" not in body
        assert 'data-section="advertisers"' not in body
        # And the read-only platform-API rationale copy goes with the section.
        assert "provisioned by your platform via the Tenant Management API" not in body

    def test_embedded_hides_advertiser_create_button(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # No "Add Buyer Agent" / "Create First Buyer Agent" CTAs, no link to /principals/create.
        assert "Add Buyer Agent" not in body
        assert "Create First Buyer Agent" not in body
        assert "/principals/create" not in body

    def test_embedded_hides_per_row_edit_and_delete_controls(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # Per-row Edit link and Delete button are gone with the section.
        assert "deletePrincipal(" not in body
        assert 'title="Edit Buyer Agent"' not in body
        assert 'title="Delete Buyer Agent"' not in body

    def test_open_tenant_shows_full_advertiser_write_ui(self, embedded_client, open_tenant_id):
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings")
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

    def test_standalone_renders_buyer_routing_pointer_banner(self, embedded_client, open_tenant_id):
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        # Headline copy locks the user-visible message.
        assert "Advertiser mapping moved to Buyer Routing" in body
        # The banner's primary CTA links to the canonical Buyer Routing page.
        assert f"/tenant/{open_tenant_id}/buyer-routing" in body

    def test_standalone_keeps_directory_visible(self, embedded_client, open_tenant_id):
        """Banner complements rather than replaces the existing section —
        the directory header, Create CTA, and table area must still render
        alongside the new banner."""
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        assert "<h2>Buyer Agent Management</h2>" in body
        assert 'data-section="advertisers"' in body
        # The /principals/create link remains the canonical Create signal.
        assert "/principals/create" in body
        # And the embedded read-only note must NOT appear here.
        assert "auto-created from request headers" not in body

    def test_embedded_does_not_render_banner(self, embedded_client, embedded_tenant_id):
        """Regression guard: the banner must sit INSIDE the
        ``{% if not tenant.is_embedded %}`` branch so it never reaches
        embedded publishers (whose copy about "managing buyer-protocol
        Principal records" doesn't apply — those rows are auto-created
        from headers)."""
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/settings")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        assert "Advertiser mapping moved to Buyer Routing" not in body


# ---------------------------------------------------------------------------
# /tenant/{id}/inventory  (Sync Inventory)
# ---------------------------------------------------------------------------


class TestSyncInventoryRenderOnOpenInstance:
    """The Sync Inventory page renders on open-instance tenants. The
    embedded hide is no longer per-tenant — Sprint 7 IA refinement (#473)
    moved it to the ``inventory_sync`` capability flag (env-driven,
    defaults to ``storefront`` on embedded). See
    ``TestInventorySyncCapabilityGate`` in
    ``test_embedded_capability_gating.py`` for the env-flag coverage of
    the embedded-side hide and redirect.

    Sync itself is driven by the upstream platform via
    ``POST /api/v1/tenant-management/tenants/{id}/refresh`` on
    storefront-owned deployments.
    """

    def test_open_tenant_renders_sync_controls(self, embedded_client, open_tenant_id):
        """Open-instance tenants see the narrowed Sync Inventory page —
        sync controls only, no Publishers / Profiles / Browse tabs."""
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/inventory")
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

    # NOTE: The Ledger dashboard redesign (PR #24) removed top-level inventory
    # nav from the tenant dashboard in favour of the Incoming/Running/Pipeline
    # editorial layout. The "Sync Inventory" link is no longer surfaced from
    # the dashboard — publishers reach Sync Inventory via the Settings rail.


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

    def test_browse_inventory_resolves_on_open_tenant(self, embedded_client, open_tenant_id):
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/inventory/browse")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_browse_inventory_resolves_on_embedded_tenant(self, embedded_client, embedded_tenant_id):
        """Embedded tenants browse inventory the platform synced for them —
        the page renders normally; only the sync trigger is suppressed
        elsewhere."""
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/inventory/browse")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_targeting_resolves_on_open_tenant(self, embedded_client, open_tenant_id):
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/targeting")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_targeting_resolves_on_embedded_tenant(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/targeting")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_inventory_profiles_resolves_on_open_tenant(self, embedded_client, open_tenant_id):
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/inventory-profiles/")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_inventory_profiles_resolves_on_embedded_tenant(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/inventory-profiles/")
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

    def test_open_browse_inventory_omits_publishers_tab(self, embedded_client, open_tenant_id):
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/inventory/browse")
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

    def test_embedded_browse_inventory_omits_publishers_tab(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/inventory/browse")
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

    def test_embedded_omits_sync_targeting_button(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/targeting")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        # The header sync button must be gone.
        assert 'id="sync-targeting-btn"' not in body
        # The page itself must still render — Targeting Criteria heading.
        assert "Targeting Criteria Browser" in body

    def test_open_renders_sync_targeting_button(self, embedded_client, open_tenant_id):
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/targeting")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        assert 'id="sync-targeting-btn"' in body
        assert "Sync Targeting Data" in body
