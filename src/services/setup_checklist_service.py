"""Setup checklist service for tracking tenant onboarding progress.

This service tracks completion of required, recommended, and optional setup tasks
to help new users understand what they need to do before taking their first order.
"""

import logging
import os
import time
from typing import Any

from flask import url_for
from sqlalchemy import func, select
from werkzeug.routing.exceptions import BuildError

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AuthorizedProperty,
    CurrencyLimit,
    GAMInventory,
    InventoryProfile,
    Principal,
    Product,
    PublisherPartner,
    Tenant,
    TenantAuthConfig,
    TenantSignal,
)

logger = logging.getLogger(__name__)


def _is_multi_tenant_mode() -> bool:
    """Check if running in multi-tenant mode.

    In multi-tenant mode (ADCP_MULTI_TENANT=true), SSO is optional because
    the platform manages authentication centrally.

    In single-tenant mode, SSO is critical because each deployment needs
    its own authentication configuration.
    """
    return os.environ.get("ADCP_MULTI_TENANT", "").lower() == "true"


# Simple time-based cache for setup status (5 minute TTL)
_setup_status_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes


# Adapter slugs that have inventory_review_state coverage data today (#485).
# FreeWheel and SpringServe land when their sync surfaces participate.
_INVENTORY_COVERAGE_TRACKED_ADAPTERS: frozenset[str] = frozenset({"google_ad_manager", "gam"})


def _build_inventory_coverage(repo: Any, tenant_ad_server: str | None) -> dict[str, Any] | None:
    """Coverage payload for the Discovery card's bundles sub-item (#485).

    Returns ``None`` for tenants on adapters we don't track yet — the widget
    falls back to a placeholder hint. For tracked adapters, returns counts
    for ad units and placements split out by review status:

    * ``total`` — synced from the adapter (denominator)
    * ``in_bundle`` — referenced by ≥1 InventoryProfile
    * ``explicitly_skipped`` — operator decided not to sell
    * ``pending`` — synced, not bundled, not skipped (the actionable bucket)

    The ``all_reviewed`` flag is True when ``pending == 0`` — the end-state
    of Job 1: every synced entity has either been bundled or skipped.
    """
    if tenant_ad_server not in _INVENTORY_COVERAGE_TRACKED_ADAPTERS:
        return None
    adapter_slug = "gam"
    ad_units = repo.coverage_summary(adapter=adapter_slug, entity_type="ad_unit")
    placements = repo.coverage_summary(adapter=adapter_slug, entity_type="placement")
    all_reviewed = ad_units["pending"] == 0 and placements["pending"] == 0
    total = ad_units["total"] + placements["total"]
    return {
        "adapter": adapter_slug,
        "ad_units": ad_units,
        "placements": placements,
        "all_reviewed": all_reviewed,
        "has_synced_inventory": total > 0,
    }


class SetupTask:
    """Represents a single setup task with status and metadata."""

    def __init__(
        self,
        key: str,
        name: str,
        description: str,
        is_complete: bool,
        action_url: str | None = None,
        details: str | None = None,
    ):
        self.key = key
        self.name = name
        self.description = description
        self.is_complete = is_complete
        self.action_url = action_url
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "is_complete": self.is_complete,
            "action_url": self.action_url,
            "details": self.details,
        }


class SetupChecklistService:
    """Service for checking tenant setup completion status."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id

    def _settings_url(self, section: str) -> str | None:
        """Tenant settings URL, anchored to a section tab.

        Section is a URL fragment (``#<section>``) consumed by the
        client-side tab switcher in ``tenant_settings.html``, not the
        ``/settings/<section>`` path parameter — the page renders all
        tabs and the anchor selects the visible one.

        Returns ``None`` outside a Flask request context. See
        :meth:`_build_url` for the why.
        """
        return self._build_url("tenants.tenant_settings", _anchor=section)

    def _route_url(self, endpoint: str) -> str | None:
        """URL for a tenant-scoped route by Flask endpoint name.

        Returns ``None`` outside a Flask request context. See
        :meth:`_build_url`.
        """
        return self._build_url(endpoint)

    def _build_url(self, endpoint: str, **kwargs: Any) -> str | None:
        """Build a URL via Flask ``url_for``, tolerating callers whose
        Flask context can't resolve admin-blueprint endpoints.

        The service runs from three contexts:

        * **Admin UI** (full Flask app) — admin pages need real URLs to
          render.
        * **Tenant Management API** (standalone Flask app, only the
          ``tenant_management_api`` blueprint registered) — ``url_for``
          raises ``werkzeug.routing.BuildError`` because the admin-UI
          endpoints aren't registered in that app. The API never reads
          ``action_url`` anyway (it surfaces ``configure_path`` from a
          static map in ``tenant_status_service._CONFIGURE_PATHS``), so
          ``None`` is correct.
        * **MCP/A2A** (Starlette via :func:`adcp.server.serve`) —
          ``validate_setup_complete`` runs inside
          ``_create_media_buy_impl``. No Flask request stack exists, so
          ``url_for`` raises ``RuntimeError``.
          ``validate_setup_complete`` reads only ``task['name']``, so an
          absent URL is harmless.

        The completion gate (``is_complete`` evaluation) is unaffected
        by this fallback — only the cosmetic "Configure" link
        disappears on the non-admin-UI paths.
        """
        try:
            return url_for(endpoint, tenant_id=self.tenant_id, **kwargs)
        except (RuntimeError, BuildError):
            return None

    @staticmethod
    def get_bulk_setup_status(tenant_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Get setup status for multiple tenants efficiently with bulk queries.

        Uses a simple time-based cache (5 minute TTL) to avoid expensive queries
        for dashboard views. Cache is cleared on tenant updates.

        ``action_url`` fields bake in the calling request's SCRIPT_NAME via
        ``url_for()``. The only cached-output consumer is
        ``core.index → templates/index.html``, which reads
        ``progress_percent`` / ``completed_count`` / ``total_count`` /
        ``ready_for_orders`` / ``critical`` (length only) — never
        ``action_url``. ``tenant_status_service`` calls the single-tenant
        ``get_setup_status`` and bypasses the cache. So a cache hit
        across embedded/non-embedded contexts cannot surface a wrong URL
        today. If a future consumer starts reading ``action_url`` from
        the bulk output, evict the cache on request boundary or scope
        it per SCRIPT_NAME.

        Args:
            tenant_ids: List of tenant IDs to check

        Returns:
            Dict mapping tenant_id to setup status dict (same format as get_setup_status)
        """
        if not tenant_ids:
            return {}

        # Check cache and separate cached vs uncached tenant IDs
        now = time.time()
        results = {}
        uncached_ids = []

        for tenant_id in tenant_ids:
            if tenant_id in _setup_status_cache:
                timestamp, cached_status = _setup_status_cache[tenant_id]
                if now - timestamp < _CACHE_TTL_SECONDS:
                    results[tenant_id] = cached_status
                else:
                    # Cache expired
                    uncached_ids.append(tenant_id)
            else:
                uncached_ids.append(tenant_id)

        # If all tenants were cached, return early
        if not uncached_ids:
            return results

        # Fetch uncached tenants

        with get_db_session() as session:
            # Bulk fetch all uncached tenants
            stmt = select(Tenant).where(Tenant.tenant_id.in_(uncached_ids))
            tenants = {t.tenant_id: t for t in session.scalars(stmt).all()}

            # Bulk count queries for all metrics (only for uncached tenants)
            # Currency limits per tenant
            currency_stmt = (
                select(CurrencyLimit.tenant_id, func.count())
                .where(CurrencyLimit.tenant_id.in_(uncached_ids))
                .group_by(CurrencyLimit.tenant_id)
            )
            currency_counts: dict[str, int] = {  # noqa: C416
                tid: count for tid, count in session.execute(currency_stmt).all()
            }

            # Currency limits with budget controls per tenant
            budget_stmt = (
                select(CurrencyLimit.tenant_id, func.count())
                .where(CurrencyLimit.tenant_id.in_(uncached_ids))
                .where(CurrencyLimit.max_daily_package_spend.isnot(None))
                .group_by(CurrencyLimit.tenant_id)
            )
            budget_limit_counts: dict[str, int] = {  # noqa: C416
                tid: count for tid, count in session.execute(budget_stmt).all()
            }

            # Authorized properties per tenant
            property_stmt = (
                select(AuthorizedProperty.tenant_id, func.count())
                .where(AuthorizedProperty.tenant_id.in_(uncached_ids))
                .group_by(AuthorizedProperty.tenant_id)
            )
            property_counts: dict[str, int] = {  # noqa: C416
                tid: count for tid, count in session.execute(property_stmt).all()
            }

            # GAM inventory per tenant
            gam_stmt = (
                select(GAMInventory.tenant_id, func.count())
                .where(GAMInventory.tenant_id.in_(uncached_ids))
                .group_by(GAMInventory.tenant_id)
            )
            gam_inventory_counts: dict[str, int] = {  # noqa: C416
                tid: count for tid, count in session.execute(gam_stmt).all()
            }

            # Products per tenant
            product_stmt = (
                select(Product.tenant_id, func.count())
                .where(Product.tenant_id.in_(uncached_ids))
                .group_by(Product.tenant_id)
            )
            product_counts: dict[str, int] = {  # noqa: C416
                tid: count for tid, count in session.execute(product_stmt).all()
            }

            # Principals per tenant
            principal_stmt = (
                select(Principal.tenant_id, func.count())
                .where(Principal.tenant_id.in_(uncached_ids))
                .group_by(Principal.tenant_id)
            )
            principal_counts: dict[str, int] = {  # noqa: C416
                tid: count for tid, count in session.execute(principal_stmt).all()
            }

            # Verified publisher partners per tenant
            verified_publisher_stmt = (
                select(PublisherPartner.tenant_id, func.count())
                .where(PublisherPartner.tenant_id.in_(uncached_ids))
                .where(PublisherPartner.is_verified == True)  # noqa: E712
                .group_by(PublisherPartner.tenant_id)
            )
            verified_publisher_counts: dict[str, int] = {  # noqa: C416
                tid: count for tid, count in session.execute(verified_publisher_stmt).all()
            }

            # Build status for each uncached tenant using pre-fetched data
            for tenant_id in uncached_ids:
                tenant = tenants.get(tenant_id)
                if not tenant:
                    continue

                # Build status using helper method with pre-fetched counts
                service = SetupChecklistService(tenant_id)
                status = service._build_status_from_data(
                    tenant=tenant,
                    currency_count=currency_counts.get(tenant_id, 0),
                    budget_limit_count=budget_limit_counts.get(tenant_id, 0),
                    property_count=property_counts.get(tenant_id, 0),
                    verified_publisher_count=verified_publisher_counts.get(tenant_id, 0),
                    gam_inventory_count=gam_inventory_counts.get(tenant_id, 0),
                    product_count=product_counts.get(tenant_id, 0),
                    principal_count=principal_counts.get(tenant_id, 0),
                )

                # Cache the result
                _setup_status_cache[tenant_id] = (now, status)
                results[tenant_id] = status

            return results

    @staticmethod
    def clear_cache(tenant_id: str | None = None):
        """Clear setup status cache for a specific tenant or all tenants.

        Args:
            tenant_id: Specific tenant to clear, or None to clear all
        """
        if tenant_id:
            _setup_status_cache.pop(tenant_id, None)
        else:
            _setup_status_cache.clear()

    def get_setup_status(self) -> dict[str, Any]:
        """Get complete setup status with all tasks categorized.

        Returns:
            Dict with critical, recommended, optional tasks and overall progress.
        """
        with get_db_session() as session:
            # Get tenant
            stmt = select(Tenant).filter_by(tenant_id=self.tenant_id)
            tenant = session.scalars(stmt).first()
            if not tenant:
                raise ValueError(f"Tenant {self.tenant_id} not found")

            # Check all tasks
            critical_tasks = self._check_critical_tasks(session, tenant)
            recommended_tasks = self._check_recommended_tasks(session, tenant)
            optional_tasks = self._check_optional_tasks(session, tenant)

            # Calculate progress
            all_tasks = critical_tasks + recommended_tasks + optional_tasks
            completed = sum(1 for task in all_tasks if task.is_complete)
            total = len(all_tasks)
            progress_percent = int(completed / total * 100) if total > 0 else 0

            # Check if ready for first order
            critical_complete = all(task.is_complete for task in critical_tasks)

            return {
                "progress_percent": progress_percent,
                "completed_count": completed,
                "total_count": total,
                "ready_for_orders": critical_complete,
                "critical": [task.to_dict() for task in critical_tasks],
                "recommended": [task.to_dict() for task in recommended_tasks],
                "optional": [task.to_dict() for task in optional_tasks],
            }

    def _build_status_from_data(
        self,
        tenant: Tenant,
        currency_count: int,
        budget_limit_count: int,
        property_count: int,
        verified_publisher_count: int,
        gam_inventory_count: int,
        product_count: int,
        principal_count: int,
    ) -> dict[str, Any]:
        """Build setup status from pre-fetched data (used by bulk query).

        Args:
            tenant: Tenant object
            currency_count: Number of currency limits
            budget_limit_count: Number of currency limits with budget controls
            property_count: Number of authorized properties
            verified_publisher_count: Number of verified publisher partners
            gam_inventory_count: Number of GAM inventory items
            product_count: Number of products
            principal_count: Number of principals

        Returns:
            Dict with same format as get_setup_status()
        """
        # Build tasks using pre-fetched counts (no session queries)
        critical_tasks = self._build_critical_tasks(
            tenant,
            currency_count,
            property_count,
            verified_publisher_count,
            gam_inventory_count,
            product_count,
            principal_count,
        )
        recommended_tasks = self._build_recommended_tasks(tenant, budget_limit_count, currency_count)
        optional_tasks = self._build_optional_tasks(tenant, currency_count)

        # Calculate progress
        all_tasks = critical_tasks + recommended_tasks + optional_tasks
        completed = sum(1 for task in all_tasks if task.is_complete)
        total = len(all_tasks)
        progress_percent = int(completed / total * 100) if total > 0 else 0

        # Check if ready for first order
        critical_complete = all(task.is_complete for task in critical_tasks)

        return {
            "progress_percent": progress_percent,
            "completed_count": completed,
            "total_count": total,
            "ready_for_orders": critical_complete,
            "critical": [task.to_dict() for task in critical_tasks],
            "recommended": [task.to_dict() for task in recommended_tasks],
            "optional": [task.to_dict() for task in optional_tasks],
        }

    def _build_aao_tasks(self, tenant: Tenant) -> list[SetupTask]:
        """AAO checklist item (Public Agent URL).

        Sprint 1.8 §6: embedded tenants with ``public_agent_url`` set don't
        see this item — the platform (Scope3) owns it. Embedded tenants
        with NULL still see it so the gap surfaces to the host product
        via the §7 setup_tasks scope=platform annotation, but with no
        action_url (the publisher can't fix it — only the host product
        can).

        For open-instance tenants, completion uses the same resolution
        chain that powers display/discovery via
        :func:`src.services.agent_url_resolver.resolve_agent_url`:
        explicit ``public_agent_url`` → ``virtual_host`` → platform-
        prefixed subdomain default. Gating only on the explicit column
        would mark working tenants (subdomain + ``SALES_AGENT_DOMAIN``
        set) as incomplete and block them from creating media buys
        despite their URL being fully reachable — the inconsistency that
        broke the live storyboard run.

        Single source of truth for both the live-session path
        (:meth:`_check_critical_tasks`) and the bulk path
        (:meth:`_build_critical_tasks`).
        """
        from src.services.agent_url_resolver import resolve_agent_url

        resolved_url = resolve_agent_url(tenant)

        # Embedded + URL set by platform → checklist item is hidden (managed
        # off-publisher). resolve_agent_url returns None for embedded
        # tenants without an explicit column, which keeps this branch tight.
        if tenant.is_embedded and tenant.public_agent_url:
            return []

        if resolved_url:
            # Open-instance with any derivable URL. Show the row as complete
            # and surface what we resolved so the operator can spot-check.
            details = f"Configured: {resolved_url}"
        elif tenant.is_embedded:
            details = (
                "Platform configuration in progress — your host product will set this. "
                "Contact your host's support team if it stays empty."
            )
        else:
            details = (
                "Set a Custom Domain on the Account screen — your agent URL is derived "
                "from it and is what publishers list in their adagents.json."
            )

        # Embedded tenants with NULL can't fix this themselves — drop the
        # action_url so the UI doesn't surface a clickable button leading to
        # a screen where the field is readonly.
        if tenant.is_embedded and not tenant.public_agent_url:
            action_url: str | None = None
        else:
            # Self-hosted: send users to the Account screen where Custom
            # Domain (the source of the derived URL) lives.
            action_url = self._settings_url("account")

        return [
            SetupTask(
                key="public_agent_url",
                name="Public Agent URL",
                description=(
                    "The agent URL publishers list in their adagents.json to "
                    "authorize this tenant. Derived from your Custom Domain "
                    "(open-instance) or the platform's shared host (embedded)."
                ),
                is_complete=bool(resolved_url),
                action_url=action_url,
                details=details,
            ),
        ]

    @staticmethod
    def _evaluate_ad_server(tenant: Tenant) -> tuple[bool, str]:
        """Compute ``(is_configured, config_details)`` for the tenant's ad server.

        Shared by ``_check_critical_tasks``, ``_build_critical_tasks``, and
        ``get_capability_ladder``. Mock adapter only counts as configured when
        ``ADCP_TESTING=true`` — otherwise it's treated as not production-ready.
        """
        if not (tenant.ad_server and tenant.ad_server != ""):
            return False, "No ad server configured"

        if tenant.is_gam_tenant:
            return True, "GAM configured - Test connection to verify"

        if tenant.ad_server == "mock":
            if os.environ.get("ADCP_TESTING") == "true":
                return True, "Mock adapter configured (test mode)"
            return False, "Mock adapter - Configure a real ad server for production"

        if tenant.ad_server in {"triton", "triton_digital", "freewheel"}:
            return True, f"{tenant.ad_server} adapter configured"

        # Unknown adapter type - show warning but don't block
        return True, f"{tenant.ad_server} adapter - verify configuration"

    def _check_critical_tasks(self, session, tenant: Tenant) -> list[SetupTask]:
        """Check critical tasks required before first order."""
        tasks = []

        # 0. AAO model: public_agent_url. First item in the checklist —
        # the salesagent can't verify any publisher's adagents.json without
        # knowing what URL it serves on.
        tasks.extend(self._build_aao_tasks(tenant))

        # 1. Ad Server FULLY CONFIGURED - CRITICAL BLOCKER
        # This is the most important task - nothing else can be done until ad server works
        ad_server_fully_configured, config_details = self._evaluate_ad_server(tenant)

        tasks.append(
            SetupTask(
                key="ad_server_connected",
                name="⚠️ Ad Server Configuration",
                description="BLOCKER: Configure and test ad server connection before proceeding with other setup",
                is_complete=ad_server_fully_configured,
                action_url=self._settings_url("adserver"),
                details=config_details,
            )
        )

        # 2. SSO Configuration - Critical for single-tenant deployments, optional for multi-tenant
        # In multi-tenant mode, the platform manages authentication centrally
        if not _is_multi_tenant_mode():
            auth_config_stmt = select(TenantAuthConfig).filter_by(tenant_id=self.tenant_id)
            auth_config = session.scalars(auth_config_stmt).first()
            sso_enabled = bool(auth_config and auth_config.oidc_enabled)
            setup_mode_disabled = bool(not tenant.auth_setup_mode) if hasattr(tenant, "auth_setup_mode") else False

            sso_details = (
                "SSO enabled and setup mode disabled"
                if sso_enabled and setup_mode_disabled
                else ("SSO enabled but setup mode still active" if sso_enabled else "SSO not configured")
            )

            tasks.append(
                SetupTask(
                    key="sso_configuration",
                    name="⚠️ Single Sign-On (SSO)",
                    description="CRITICAL: Configure SSO and disable setup mode for production security",
                    is_complete=sso_enabled and setup_mode_disabled,
                    action_url=self._route_url("users.list_users"),
                    details=sso_details,
                )
            )

        # 3. Currency Limits - Only show after ad server is configured (GAM auto-configures currency)
        # Skip this task if no real ad server is configured yet
        if ad_server_fully_configured:
            stmt = select(func.count()).select_from(CurrencyLimit).where(CurrencyLimit.tenant_id == self.tenant_id)
            currency_count = session.scalar(stmt) or 0
            tasks.append(
                SetupTask(
                    key="currency_limits",
                    name="Currency Configuration",
                    description="At least one currency must be configured for media buys",
                    is_complete=currency_count > 0,
                    action_url=self._route_url("settings.policies_page"),
                    details=(
                        f"{currency_count} currencies configured" if currency_count > 0 else "No currencies configured"
                    ),
                )
            )

        # 4. Authorized Properties → green when EITHER:
        #    - ≥1 verified PublisherPartner (new AAO model — each
        #      partner's brand.json + adagents.json is the source of truth)
        #    - ≥1 AuthorizedProperty row (legacy model — pre-AAO tenants
        #      and existing fixtures still seed this table directly)
        # The OR keeps existing tenants out of the regression even before
        # they migrate to the AAO model.
        stmt_publishers = (
            select(func.count())
            .select_from(PublisherPartner)
            .where(
                PublisherPartner.tenant_id == self.tenant_id,
                PublisherPartner.is_verified == True,  # noqa: E712
            )
        )
        verified_publisher_count = session.scalar(stmt_publishers) or 0
        stmt_props = (
            select(func.count()).select_from(AuthorizedProperty).where(AuthorizedProperty.tenant_id == self.tenant_id)
        )
        legacy_property_count = session.scalar(stmt_props) or 0
        is_complete = verified_publisher_count > 0 or legacy_property_count > 0
        if verified_publisher_count > 0:
            details = f"{verified_publisher_count} verified publisher partners"
        elif legacy_property_count > 0:
            details = (
                f"{legacy_property_count} authorized properties (legacy mode — "
                "add a publisher partner to migrate to the AAO model)"
            )
        else:
            details = "Add a publisher partner — their adagents.json must authorize this tenant's agent URL."

        tasks.append(
            SetupTask(
                key="authorized_properties",
                name="Authorized Properties",
                description="At least one publisher partner whose adagents.json authorizes your agent URL.",
                is_complete=is_complete,
                action_url=self._route_url("publisher_partners.publishers_page"),
                details=details,
            )
        )

        # 4. Inventory Synced - Only show after ad server is configured
        if ad_server_fully_configured:
            if tenant.is_gam_tenant:
                # GAM requires syncing inventory from Google Ad Manager
                stmt = select(func.count()).select_from(GAMInventory).where(GAMInventory.tenant_id == self.tenant_id)
                inventory_count = session.scalar(stmt) or 0

                inventory_synced = inventory_count > 0
                inventory_details = (
                    f"{inventory_count:,} inventory items synced"
                    if inventory_synced
                    else "No inventory synced from ad server"
                )
                tasks.append(
                    SetupTask(
                        key="inventory_synced",
                        name="Inventory Sync",
                        description="Sync ad units and placements from ad server",
                        is_complete=inventory_synced,
                        action_url=self._route_url("inventory.inventory_browser"),
                        details=inventory_details,
                    )
                )
            elif tenant.ad_server in {"triton", "triton_digital", "freewheel"}:
                # Schema-driven adapters configure inventory per-product (not via sync).
                tasks.append(
                    SetupTask(
                        key="inventory_synced",
                        name="Inventory Configuration",
                        description=f"{tenant.ad_server.title()} adapter - inventory configured per product",
                        is_complete=True,
                        action_url=None,
                        details=f"{tenant.ad_server.title()} adapter configures inventory targeting at product level",
                    )
                )
            else:
                # Other adapters - show as complete but with note to verify
                tasks.append(
                    SetupTask(
                        key="inventory_synced",
                        name="Inventory Configuration",
                        description="Inventory configuration - check adapter documentation",
                        is_complete=True,
                        action_url=None,
                        details=f"{tenant.ad_server} adapter - verify inventory configuration requirements",
                    )
                )

        # 5. Products Created - Only show after ad server is configured
        if ad_server_fully_configured:
            stmt = select(func.count()).select_from(Product).where(Product.tenant_id == self.tenant_id)
            product_count = session.scalar(stmt) or 0
            tasks.append(
                SetupTask(
                    key="products_created",
                    name="Products",
                    description="Create at least one advertising product",
                    is_complete=product_count > 0,
                    action_url=self._route_url("products.list_products"),
                    details=f"{product_count} products created" if product_count > 0 else "No products created",
                )
            )

        # 6. Principals Created — Sprint 7 IA cleanup: skip on embedded.
        # Principal provisioning on embedded tenants is platform-managed
        # (Tenant Management API creates them on /provision and via the
        # embedded auth header bypass), so there's nothing for the publisher
        # operator to do here. The Buyer Agents settings tab is also hidden
        # on embedded; surfacing this task would link to a missing section.
        if not tenant.is_embedded:
            stmt = select(func.count()).select_from(Principal).where(Principal.tenant_id == self.tenant_id)
            principal_count = session.scalar(stmt) or 0
            tasks.append(
                SetupTask(
                    key="principals_created",
                    name="Advertisers (Principals)",
                    description="Create principals for advertisers who will buy inventory",
                    is_complete=principal_count > 0,
                    action_url=self._settings_url("advertisers"),
                    details=(
                        f"{principal_count} advertisers configured"
                        if principal_count > 0
                        else "No advertisers configured"
                    ),
                )
            )

        return tasks

    def _check_recommended_tasks(self, session, tenant: Tenant) -> list[SetupTask]:
        """Check recommended tasks for better experience."""
        tasks = []

        # 1. Tenant Name (important for branding)
        # Default names that indicate user hasn't customized
        default_names = {"default", "Test Sales Agent", "My Sales Agent", "Demo Sales Agent"}
        has_custom_name = bool(tenant.name and tenant.name not in default_names and tenant.name != tenant.tenant_id)
        tasks.append(
            SetupTask(
                key="tenant_name",
                name="Account Name",
                description="Set a display name for your sales agent",
                is_complete=has_custom_name,
                action_url=self._settings_url("account"),
                details=f"Using '{tenant.name}'" if has_custom_name else "Using default name",
            )
        )

        # 2. Creative Approval Guidelines
        # Only count as configured if user has set auto-approve formats (explicit configuration)
        # Default human_review_required=True doesn't count as "configured"
        has_approval_config = bool(tenant.auto_approve_format_ids)
        tasks.append(
            SetupTask(
                key="creative_approval_guidelines",
                name="Creative Approval Guidelines",
                description="Configure auto-approval rules and manual review settings",
                is_complete=has_approval_config,
                action_url=self._route_url("settings.policies_page"),
                details=(
                    "Auto-approval formats configured"
                    if has_approval_config
                    else "Using default (manual review required)"
                ),
            )
        )

        # 3. Naming Conventions
        # Only count line_item_name_template as custom (order_name_template has server_default)
        has_custom_naming = bool(tenant.line_item_name_template)
        tasks.append(
            SetupTask(
                key="naming_conventions",
                name="Naming Conventions",
                description="Customize order and line item naming templates",
                is_complete=has_custom_naming,
                action_url=self._route_url("settings.policies_page"),
                details="Custom templates configured" if has_custom_naming else "Using default naming templates",
            )
        )

        # 3. Budget Controls
        # Check if any currency limit has max_daily_package_spend set
        stmt = (
            select(func.count())
            .select_from(CurrencyLimit)
            .where(CurrencyLimit.tenant_id == self.tenant_id)
            .where(CurrencyLimit.max_daily_package_spend.isnot(None))
        )
        budget_limit_count = session.scalar(stmt) or 0
        has_budget_limits = budget_limit_count > 0

        details = (
            f"{budget_limit_count} currency limit(s) with daily budget controls"
            if has_budget_limits
            else "Budget limits can be set per currency"
        )

        tasks.append(
            SetupTask(
                key="budget_controls",
                name="Budget Controls",
                description="Set maximum daily budget limits for safety",
                is_complete=has_budget_limits,
                action_url=self._route_url("settings.policies_page"),
                details=details,
            )
        )

        # 4. AXE Segment Keys Configuration (RECOMMENDED - part of AdCP spec)
        # AXE (Audience Exchange) targeting is part of the AdCP protocol specification
        # This is recommended but not required - media buys can be created without AXE targeting
        adapter_config = tenant.adapter_config
        has_axe_include = bool(adapter_config and adapter_config.axe_include_key)
        has_axe_exclude = bool(adapter_config and adapter_config.axe_exclude_key)
        has_axe_macro = bool(adapter_config and adapter_config.axe_macro_key)
        # All three keys should be configured for full AdCP compliance
        axe_keys_configured = has_axe_include and has_axe_exclude and has_axe_macro

        axe_details = []
        if has_axe_include:
            axe_details.append(f"Include: {adapter_config.axe_include_key}")
        if has_axe_exclude:
            axe_details.append(f"Exclude: {adapter_config.axe_exclude_key}")
        if has_axe_macro:
            axe_details.append(f"Macro: {adapter_config.axe_macro_key}")

        tasks.append(
            SetupTask(
                key="axe_segment_keys",
                name="AXE Segment Keys",
                description="Configure custom targeting keys for AXE audience segments (recommended for AdCP compliance)",
                is_complete=axe_keys_configured,
                action_url=self._route_url("inventory.targeting_browser"),
                details=(
                    ", ".join(axe_details)
                    if axe_keys_configured
                    else f"Configure all three keys: include, exclude, macro ({len(axe_details)}/3 configured)"
                ),
            )
        )

        # 5. Slack Integration
        slack_webhook = tenant.slack_webhook_url
        slack_configured = bool(slack_webhook)
        tasks.append(
            SetupTask(
                key="slack_integration",
                name="Slack Integration",
                description="Configure Slack webhooks for order notifications",
                is_complete=slack_configured,
                action_url=self._route_url("settings.integrations_page"),
                details="Slack notifications enabled" if slack_configured else "No Slack integration",
            )
        )

        # 6. Tenant CNAME (Virtual Host)
        virtual_host = tenant.virtual_host
        has_custom_domain = bool(virtual_host)
        tasks.append(
            SetupTask(
                key="tenant_cname",
                name="Custom Domain (CNAME)",
                description="Configure custom domain for your sales agent",
                is_complete=has_custom_domain,
                action_url=self._settings_url("account"),
                details=f"Using {virtual_host}" if has_custom_domain else "Using default subdomain",
            )
        )

        return tasks

    def _check_optional_tasks(self, session, tenant: Tenant) -> list[SetupTask]:
        """Check optional enhancement tasks."""
        tasks = []

        # SSO Configuration - Optional in multi-tenant mode (platform manages auth centrally)
        # In single-tenant mode, SSO is critical and shown there instead
        if _is_multi_tenant_mode():
            auth_config_stmt = select(TenantAuthConfig).filter_by(tenant_id=self.tenant_id)
            auth_config = session.scalars(auth_config_stmt).first()
            sso_enabled = bool(auth_config and auth_config.oidc_enabled)
            setup_mode_disabled = bool(not tenant.auth_setup_mode) if hasattr(tenant, "auth_setup_mode") else False

            sso_details = (
                "SSO enabled and setup mode disabled"
                if sso_enabled and setup_mode_disabled
                else ("SSO enabled but setup mode still active" if sso_enabled else "SSO not configured")
            )

            tasks.append(
                SetupTask(
                    key="sso_configuration",
                    name="Single Sign-On (SSO)",
                    description="Configure tenant-specific SSO authentication",
                    is_complete=sso_enabled and setup_mode_disabled,
                    action_url=self._route_url("users.list_users"),
                    details=sso_details,
                )
            )

        # 1. Signals Discovery Agent
        signals_enabled = tenant.enable_axe_signals or False
        tasks.append(
            SetupTask(
                key="signals_agent",
                name="Signals Discovery Agent",
                description="Enable AXE signals for advanced targeting",
                is_complete=signals_enabled,
                action_url=self._route_url("settings.integrations_page"),
                details="AXE signals enabled" if signals_enabled else "AXE signals not configured",
            )
        )

        # 2. Gemini AI Features (Optional - Tenant-Specific)
        gemini_configured = bool(tenant.gemini_api_key)
        tasks.append(
            SetupTask(
                key="gemini_api_key",
                name="Gemini AI Features",
                description="Enable AI-assisted product recommendations and creative policy checks",
                is_complete=gemini_configured,
                action_url=self._route_url("settings.integrations_page"),
                details=(
                    "AI features enabled" if gemini_configured else "Optional: Configure Gemini API key for AI features"
                ),
            )
        )

        # 3. Multiple Currencies
        stmt = select(func.count()).select_from(CurrencyLimit).where(CurrencyLimit.tenant_id == self.tenant_id)
        currency_count = session.scalar(stmt) or 0
        multiple_currencies = currency_count > 1
        tasks.append(
            SetupTask(
                key="multiple_currencies",
                name="Multiple Currencies",
                description="Support international advertisers with EUR, GBP, etc.",
                is_complete=multiple_currencies,
                action_url=self._route_url("settings.policies_page"),
                details=(
                    f"{currency_count} currencies supported" if multiple_currencies else "Only 1 currency configured"
                ),
            )
        )

        return tasks

    def _build_critical_tasks(
        self,
        tenant: Tenant,
        currency_count: int,
        property_count: int,
        verified_publisher_count: int,
        gam_inventory_count: int,
        product_count: int,
        principal_count: int,
    ) -> list[SetupTask]:
        """Build critical tasks from pre-fetched data (no session queries).

        Mirrors :meth:`_check_critical_tasks` for the bulk path; the two
        must stay in sync so single-tenant and bulk callers agree on
        ``progress_percent`` and ``ready_for_orders``.
        """
        tasks = list(self._build_aao_tasks(tenant))

        # 1. Ad Server Configuration
        ad_server_fully_configured, config_details = self._evaluate_ad_server(tenant)

        tasks.append(
            SetupTask(
                key="ad_server_connected",
                name="⚠️ Ad Server Configuration",
                description="BLOCKER: Configure and test ad server connection before proceeding with other setup",
                is_complete=ad_server_fully_configured,
                action_url=self._settings_url("adserver"),
                details=config_details,
            )
        )

        # 2. SSO Configuration - Critical for single-tenant deployments, optional for multi-tenant
        if not _is_multi_tenant_mode():
            auth_config = tenant.auth_config if hasattr(tenant, "auth_config") else None
            sso_enabled = bool(auth_config and auth_config.oidc_enabled)
            setup_mode_disabled = bool(not tenant.auth_setup_mode) if hasattr(tenant, "auth_setup_mode") else False

            sso_details = (
                "SSO enabled and setup mode disabled"
                if sso_enabled and setup_mode_disabled
                else ("SSO enabled but setup mode still active" if sso_enabled else "SSO not configured")
            )

            tasks.append(
                SetupTask(
                    key="sso_configuration",
                    name="⚠️ Single Sign-On (SSO)",
                    description="CRITICAL: Configure SSO and disable setup mode for production security",
                    is_complete=sso_enabled and setup_mode_disabled,
                    action_url=self._route_url("users.list_users"),
                    details=sso_details,
                )
            )

        # 3. Currency Limits - Only show after ad server is configured
        if ad_server_fully_configured:
            tasks.append(
                SetupTask(
                    key="currency_limits",
                    name="Currency Configuration",
                    description="At least one currency must be configured for media buys",
                    is_complete=currency_count > 0,
                    action_url=self._route_url("settings.policies_page"),
                    details=(
                        f"{currency_count} currencies configured" if currency_count > 0 else "No currencies configured"
                    ),
                )
            )

        # 4. Authorized Properties
        # Single source of truth: AuthorizedProperty table
        # (Populated automatically when syncing verified PublisherPartners)
        # Note: property_count and verified_publisher_count are passed as parameters (pre-fetched)
        properties_is_complete = property_count > 0
        properties_details = (
            f"{property_count} properties from {verified_publisher_count} verified publishers"
            if property_count > 0
            else "Add publishers and sync to discover properties"
        )

        tasks.append(
            SetupTask(
                key="authorized_properties",
                name="Authorized Properties",
                description="Configure properties with adagents.json for verification",
                is_complete=properties_is_complete,
                action_url=self._route_url("publisher_partners.publishers_page"),
                details=properties_details,
            )
        )

        # 4. Inventory Synced - Only show after ad server is configured
        if ad_server_fully_configured:
            if tenant.is_gam_tenant:
                inventory_synced = gam_inventory_count > 0
                tasks.append(
                    SetupTask(
                        key="inventory_synced",
                        name="Inventory Sync",
                        description="Sync ad units and placements from ad server",
                        is_complete=inventory_synced,
                        action_url=self._route_url("inventory.inventory_browser"),
                        details=(
                            f"{gam_inventory_count:,} inventory items synced"
                            if inventory_synced
                            else "No inventory synced from ad server"
                        ),
                    )
                )
            elif tenant.ad_server in {"triton", "triton_digital", "freewheel"}:
                tasks.append(
                    SetupTask(
                        key="inventory_synced",
                        name="Inventory Configuration",
                        description=f"{tenant.ad_server.title()} adapter - inventory configured per product",
                        is_complete=True,
                        action_url=None,
                        details=f"{tenant.ad_server.title()} adapter configures inventory targeting at product level",
                    )
                )
            else:
                tasks.append(
                    SetupTask(
                        key="inventory_synced",
                        name="Inventory Configuration",
                        description=f"{tenant.ad_server} adapter - inventory configured per product",
                        is_complete=True,
                        action_url=None,
                        details=f"{tenant.ad_server} adapter configures inventory targeting at product level",
                    )
                )

        # 5. Products Created - Only show after ad server is configured
        if ad_server_fully_configured:
            tasks.append(
                SetupTask(
                    key="products_created",
                    name="Products",
                    description="Create at least one advertising product",
                    is_complete=product_count > 0,
                    action_url=self._route_url("products.list_products"),
                    details=f"{product_count} products created" if product_count > 0 else "No products created",
                )
            )

        # 6. Principals Created — Sprint 7 IA cleanup: skip on embedded.
        # See :meth:`_check_critical_tasks` for the full rationale. Mirroring
        # the per-tenant path so single-tenant and bulk callers agree on
        # ``progress_percent`` and ``ready_for_orders``.
        if not tenant.is_embedded:
            tasks.append(
                SetupTask(
                    key="principals_created",
                    name="Advertisers (Principals)",
                    description="Create principals for advertisers who will buy inventory",
                    is_complete=principal_count > 0,
                    action_url=self._settings_url("advertisers"),
                    details=(
                        f"{principal_count} advertisers configured"
                        if principal_count > 0
                        else "No advertisers configured"
                    ),
                )
            )

        return tasks

    def _build_recommended_tasks(self, tenant: Tenant, budget_limit_count: int, currency_count: int) -> list[SetupTask]:
        """Build recommended tasks from pre-fetched data (no session queries)."""
        tasks = []

        # 1. Tenant Name (important for branding)
        # Default names that indicate user hasn't customized
        default_names = {"default", "Test Sales Agent", "My Sales Agent", "Demo Sales Agent"}
        has_custom_name = bool(tenant.name and tenant.name not in default_names and tenant.name != tenant.tenant_id)
        tasks.append(
            SetupTask(
                key="tenant_name",
                name="Account Name",
                description="Set a display name for your sales agent",
                is_complete=has_custom_name,
                action_url=self._settings_url("account"),
                details=f"Using '{tenant.name}'" if has_custom_name else "Using default name",
            )
        )

        # 2. Creative Approval Guidelines
        # Only count as configured if user has set auto-approve formats (explicit configuration)
        # Default human_review_required=True doesn't count as "configured"
        has_approval_config = bool(tenant.auto_approve_format_ids)
        tasks.append(
            SetupTask(
                key="creative_approval_guidelines",
                name="Creative Approval Guidelines",
                description="Configure auto-approval rules and manual review settings",
                is_complete=has_approval_config,
                action_url=self._route_url("settings.policies_page"),
                details=(
                    "Auto-approval formats configured"
                    if has_approval_config
                    else "Using default (manual review required)"
                ),
            )
        )

        # 3. Naming Conventions
        # Only count line_item_name_template as custom (order_name_template has server_default)
        has_custom_naming = bool(tenant.line_item_name_template)
        tasks.append(
            SetupTask(
                key="naming_conventions",
                name="Naming Conventions",
                description="Customize order and line item naming templates",
                is_complete=has_custom_naming,
                action_url=self._route_url("settings.policies_page"),
                details="Custom templates configured" if has_custom_naming else "Using default naming templates",
            )
        )

        # 3. Budget Controls
        has_budget_limits = budget_limit_count > 0
        tasks.append(
            SetupTask(
                key="budget_controls",
                name="Budget Controls",
                description="Set maximum daily budget limits for safety",
                is_complete=has_budget_limits,
                action_url=self._route_url("settings.policies_page"),
                details=(
                    f"{budget_limit_count} currency limit(s) with daily budget controls"
                    if has_budget_limits
                    else "Budget limits can be set per currency"
                ),
            )
        )

        # 4. AXE Segment Keys Configuration (RECOMMENDED - part of AdCP spec)
        # AXE (Audience Exchange) targeting is part of the AdCP protocol specification
        # This is recommended but not required - media buys can be created without AXE targeting
        adapter_config = tenant.adapter_config
        has_axe_include = bool(adapter_config and adapter_config.axe_include_key)
        has_axe_exclude = bool(adapter_config and adapter_config.axe_exclude_key)
        has_axe_macro = bool(adapter_config and adapter_config.axe_macro_key)
        # All three keys should be configured for full AdCP compliance
        axe_keys_configured = has_axe_include and has_axe_exclude and has_axe_macro

        axe_details = []
        if has_axe_include:
            axe_details.append(f"Include: {adapter_config.axe_include_key}")
        if has_axe_exclude:
            axe_details.append(f"Exclude: {adapter_config.axe_exclude_key}")
        if has_axe_macro:
            axe_details.append(f"Macro: {adapter_config.axe_macro_key}")

        tasks.append(
            SetupTask(
                key="axe_segment_keys",
                name="AXE Segment Keys",
                description="Configure custom targeting keys for AXE audience segments (recommended for AdCP compliance)",
                is_complete=axe_keys_configured,
                action_url=self._route_url("inventory.targeting_browser"),
                details=(
                    ", ".join(axe_details)
                    if axe_keys_configured
                    else f"Configure all three keys: include, exclude, macro ({len(axe_details)}/3 configured)"
                ),
            )
        )

        # 5. Slack Integration
        slack_configured = bool(tenant.slack_webhook_url)
        tasks.append(
            SetupTask(
                key="slack_integration",
                name="Slack Integration",
                description="Configure Slack webhooks for order notifications",
                is_complete=slack_configured,
                action_url=self._route_url("settings.integrations_page"),
                details="Slack notifications enabled" if slack_configured else "No Slack integration",
            )
        )

        # 6. Custom Domain
        has_custom_domain = bool(tenant.virtual_host)
        tasks.append(
            SetupTask(
                key="tenant_cname",
                name="Custom Domain (CNAME)",
                description="Configure custom domain for your sales agent",
                is_complete=has_custom_domain,
                action_url=self._settings_url("account"),
                details=f"Using {tenant.virtual_host}" if has_custom_domain else "Using default subdomain",
            )
        )

        return tasks

    def _build_optional_tasks(self, tenant: Tenant, currency_count: int) -> list[SetupTask]:
        """Build optional tasks from pre-fetched data (no session queries)."""
        tasks = []

        # SSO Configuration - Optional in multi-tenant mode (platform manages auth centrally)
        # In single-tenant mode, SSO is critical and shown there instead
        if _is_multi_tenant_mode():
            auth_config = tenant.auth_config if hasattr(tenant, "auth_config") else None
            sso_enabled = bool(auth_config and auth_config.oidc_enabled)
            setup_mode_disabled = bool(not tenant.auth_setup_mode) if hasattr(tenant, "auth_setup_mode") else False

            sso_details = (
                "SSO enabled and setup mode disabled"
                if sso_enabled and setup_mode_disabled
                else ("SSO enabled but setup mode still active" if sso_enabled else "SSO not configured")
            )

            tasks.append(
                SetupTask(
                    key="sso_configuration",
                    name="Single Sign-On (SSO)",
                    description="Configure tenant-specific SSO authentication",
                    is_complete=sso_enabled and setup_mode_disabled,
                    action_url=self._route_url("users.list_users"),
                    details=sso_details,
                )
            )

        # 1. Signals Discovery Agent
        signals_enabled = tenant.enable_axe_signals or False
        tasks.append(
            SetupTask(
                key="signals_agent",
                name="Signals Discovery Agent",
                description="Enable AXE signals for advanced targeting",
                is_complete=signals_enabled,
                action_url=self._route_url("settings.integrations_page"),
                details="AXE signals enabled" if signals_enabled else "AXE signals not configured",
            )
        )

        # 2. Gemini AI Features
        gemini_configured = bool(tenant.gemini_api_key)
        tasks.append(
            SetupTask(
                key="gemini_api_key",
                name="Gemini AI Features",
                description="Enable AI-assisted product recommendations and creative policy checks",
                is_complete=gemini_configured,
                action_url=self._route_url("settings.integrations_page"),
                details=(
                    "AI features enabled" if gemini_configured else "Optional: Configure Gemini API key for AI features"
                ),
            )
        )

        # 3. Multiple Currencies
        multiple_currencies = currency_count > 1
        tasks.append(
            SetupTask(
                key="multiple_currencies",
                name="Multiple Currencies",
                description="Support international advertisers with EUR, GBP, etc.",
                is_complete=multiple_currencies,
                action_url=self._route_url("settings.policies_page"),
                details=(
                    f"{currency_count} currencies supported" if multiple_currencies else "Only 1 currency configured"
                ),
            )
        )

        return tasks

    def get_dashboard_jobs(self) -> dict[str, Any]:
        """Compute the three ongoing seller jobs for the dashboard (#471).

        The operator's dashboard isn't a setup wizard with an "ready" state —
        it's a workbench for three persistent jobs:

        * **Discovery & Matching** — *the* primary job; the reason the
          operator opens the dashboard. "Have I exposed the right
          inventory and signals so buyers can find them?" Today the widget
          surfaces bundle + signal counts; the richer coverage analytics
          (what fraction of ad units / placements / KVs / audiences are
          exposed vs. reviewed-and-explicitly-skipped) land in follow-up
          issues. Adapter-agnostic in principle (GAM today; FreeWheel,
          SpringServe, etc. as their syncs land).

        * **Composition** — combine catalog into buyer-facing products.
          Static product CRUD today; dynamic composition (price ×
          optimization × targeting × demand) is the direction. Hidden for
          embedded tenants: composition runs upstream in the storefront.

        * **Delivery** — fulfill the orders you've sold. Approvals,
          pacing, exceptions. Light here — the existing Pipeline strip
          below this widget shows the live state. This card is the
          jumping-off point.

        These are **ongoing jobs**, not a sequence to complete; the widget
        is always shown. Distinct from the hygiene gate
        (:meth:`validate_setup_complete`) — that gates whether the agent
        can take orders at all; these are the operator's day-to-day work.
        """
        from src.core.database.repositories.tenant_config import TenantConfigRepository

        with get_db_session() as session:
            tenant = TenantConfigRepository(session, self.tenant_id).get_tenant()
            if not tenant:
                raise ValueError(f"Tenant {self.tenant_id} not found")

            inventory_bundle_count = (
                session.scalar(
                    select(func.count())
                    .select_from(InventoryProfile)
                    .where(InventoryProfile.tenant_id == self.tenant_id)
                )
                or 0
            )
            signal_profile_count = (
                session.scalar(
                    select(func.count()).select_from(TenantSignal).where(TenantSignal.tenant_id == self.tenant_id)
                )
                or 0
            )
            product_count = (
                session.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == self.tenant_id))
                or 0
            )

            # Inventory coverage analytics (#485). Only computed for tenants
            # on an adapter we track (GAM today). Embedded tenants don't see
            # publisher-side inventory coverage — their storefront drives the
            # narrative — but they can still expose bundles, so the count
            # remains accurate.
            from src.core.database.repositories.inventory_review_state import (
                InventoryReviewStateRepository,
            )

            inventory_coverage = _build_inventory_coverage(
                InventoryReviewStateRepository(session, self.tenant_id),
                tenant_ad_server=tenant.ad_server,
            )

            discovery_job: dict[str, Any] = {
                "key": "discovery",
                "name": "Product discovery & matching",
                "tagline": "Make sure buyers can find the right inventory and signals from you.",
                "sub_items": [
                    {
                        "key": "bundles",
                        "name": "Inventory bundles",
                        "count": inventory_bundle_count,
                        "started": inventory_bundle_count > 0,
                        "action_url": self._route_url("inventory_profiles.list_inventory_profiles"),
                        "action_label": "Review bundles" if inventory_bundle_count > 0 else "Author bundles",
                        # Real inventory coverage analytics (#485). ``None`` for adapters we
                        # don't track yet — the widget falls back to a placeholder hint.
                        "coverage": inventory_coverage,
                    },
                    {
                        "key": "signals",
                        "name": "Signal profiles",
                        "count": signal_profile_count,
                        # Signals are optional — a publisher with zero signals is valid, but only
                        # if they've reviewed their signal universe and made the call. Signal
                        # coverage analytics land in #486 (parallel work).
                        "started": signal_profile_count > 0,
                        "action_url": self._route_url("tenant_signals.list_signals"),
                        "action_label": "Review signals" if signal_profile_count > 0 else "Author signals",
                        "coverage": None,
                    },
                ],
            }

            # Composition job — hidden entirely for embedded. The storefront
            # owns composition; surfacing it on a publisher dashboard would
            # mislead.
            composition_job: dict[str, Any] | None = None
            if not tenant.is_embedded:
                composition_job = {
                    "key": "composition",
                    "name": "Composition",
                    "tagline": "Combine your catalog into buyer-facing products.",
                    "count": product_count,
                    "count_label": "product" if product_count == 1 else "products",
                    "action_url": self._route_url("products.list_products"),
                    "action_label": "Manage products" if product_count > 0 else "Compose a product",
                    # Static CRUD is the present-tense path; dynamic composition is the direction.
                    "note": "Static products today. Dynamic composition (pricing × targeting × demand) is the direction.",
                }

            # Delivery job — light surface. The detailed pipeline (incoming /
            # running / pending) is the existing strip below this widget.
            delivery_job: dict[str, Any] = {
                "key": "delivery",
                "name": "Delivery",
                "tagline": "Fulfill the orders you've sold.",
                "action_url": self._route_url("operations.reporting"),
                "action_label": "Reporting",
                "note": "Approvals, pacing, and exceptions live in the pipeline below.",
            }

            jobs: list[dict[str, Any]] = [discovery_job]
            if composition_job is not None:
                jobs.append(composition_job)
            jobs.append(delivery_job)

            return {
                "is_embedded": tenant.is_embedded,
                "jobs": jobs,
            }

    def get_next_steps(self) -> list[dict[str, str]]:
        """Get prioritized next steps for incomplete tasks.

        Returns:
            List of next steps with title, description, and action URL.
        """
        status = self.get_setup_status()
        next_steps = []

        # Prioritize critical tasks first
        for task in status["critical"]:
            if not task["is_complete"]:
                next_steps.append(
                    {
                        "title": task["name"],
                        "description": task["description"],
                        "action_url": task["action_url"],
                        "priority": "critical",
                    }
                )

        # Then recommended tasks
        for task in status["recommended"]:
            if not task["is_complete"]:
                next_steps.append(
                    {
                        "title": task["name"],
                        "description": task["description"],
                        "action_url": task["action_url"],
                        "priority": "recommended",
                    }
                )

        # Limit to top 3 next steps
        return next_steps[:3]


class SetupIncompleteError(Exception):
    """Raised when attempting operations that require complete setup."""

    def __init__(self, message: str, missing_tasks: list[dict]):
        self.message = message
        self.missing_tasks = missing_tasks
        super().__init__(self.message)


def get_incomplete_critical_tasks(tenant_id: str) -> list[dict[str, Any]]:
    """Get list of incomplete critical tasks for a tenant.

    Args:
        tenant_id: Tenant ID to check

    Returns:
        List of incomplete critical task dictionaries
    """
    service = SetupChecklistService(tenant_id)
    status = service.get_setup_status()
    return [task for task in status["critical"] if not task["is_complete"]]


def validate_setup_complete(tenant_id: str) -> None:
    """Validate that tenant has completed all critical setup tasks.

    Embedded-mode tenants skip this gate: platform-config tasks (ad server,
    SSO, etc.) are owned by the host product (Storefront, Manticore, ...),
    not the publisher, and surfacing them as buyer-protocol blockers would
    be wrong.
    """
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.tenant_config import TenantConfigRepository

    with get_db_session() as session:
        tenant = TenantConfigRepository(session, tenant_id).get_tenant()
        if tenant and tenant.is_embedded:
            return

    incomplete = get_incomplete_critical_tasks(tenant_id)
    if incomplete:
        task_names = ", ".join(task["name"] for task in incomplete)
        raise SetupIncompleteError(
            f"Complete required setup tasks before creating orders: {task_names}", missing_tasks=incomplete
        )
