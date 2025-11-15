"""Setup checklist service for tracking tenant onboarding progress.

This service tracks completion of required, recommended, and optional setup tasks
to help new users understand what they need to do before taking their first order.
"""

import logging
import time
from typing import Any

from sqlalchemy import func, select

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AuthorizedProperty,
    CurrencyLimit,
    GAMInventory,
    Principal,
    Product,
    Tenant,
)

logger = logging.getLogger(__name__)

# Simple time-based cache for setup status (5 minute TTL)
_setup_status_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes


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

    @staticmethod
    def get_bulk_setup_status(tenant_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Get setup status for multiple tenants efficiently with bulk queries.

        Uses a simple time-based cache (5 minute TTL) to avoid expensive queries
        for dashboard views. Cache is cleared on tenant updates.

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
            gam_inventory_count: Number of GAM inventory items
            product_count: Number of products
            principal_count: Number of principals

        Returns:
            Dict with same format as get_setup_status()
        """
        # Build tasks using pre-fetched counts (no session queries)
        critical_tasks = self._build_critical_tasks(
            tenant, currency_count, property_count, gam_inventory_count, product_count, principal_count
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

    def _check_critical_tasks(self, session, tenant: Tenant) -> list[SetupTask]:
        """Check critical tasks required before first order."""
        tasks = []

        # 1. Ad Server FULLY CONFIGURED - CRITICAL BLOCKER
        # This is the most important task - nothing else can be done until ad server works
        ad_server_selected = tenant.ad_server is not None and tenant.ad_server != ""

        # For GAM, check that it's fully configured with OAuth credentials
        ad_server_fully_configured = False
        config_details = "No ad server configured"

        if ad_server_selected:
            if tenant.ad_server == "google_ad_manager":
                # Check if GAM has OAuth tokens (indicates successful authentication)
                # GAM config is stored in the adapter_config table, not directly on tenant
                # For now, just check if adapter is selected
                has_credentials = True  # Assume configured if GAM is selected
                ad_server_fully_configured = has_credentials

                if has_credentials:
                    config_details = "GAM configured - Test connection to verify"
                else:
                    config_details = "GAM selected but not authenticated - Complete OAuth flow and test connection"
            elif tenant.ad_server == "mock":
                # Mock adapter is always ready once selected
                ad_server_fully_configured = True
                config_details = "Mock adapter configured - Ready for testing"
            elif tenant.ad_server in ["kevel", "triton"]:
                # Other adapters (Kevel, Triton) - assume configured once selected
                ad_server_fully_configured = True
                config_details = f"{tenant.ad_server} adapter configured"
            else:
                # Unknown adapter type - show warning but don't block
                ad_server_fully_configured = True
                config_details = f"{tenant.ad_server} adapter - verify configuration"

        tasks.append(
            SetupTask(
                key="ad_server_connected",
                name="⚠️ Ad Server Configuration",
                description="BLOCKER: Configure and test ad server connection before proceeding with other setup",
                is_complete=ad_server_fully_configured,
                action_url=f"/tenant/{self.tenant_id}/settings#adserver",
                details=config_details,
            )
        )

        # 2. Currency Limits
        stmt = select(func.count()).select_from(CurrencyLimit).where(CurrencyLimit.tenant_id == self.tenant_id)
        currency_count = session.scalar(stmt) or 0
        tasks.append(
            SetupTask(
                key="currency_limits",
                name="Currency Configuration",
                description="At least one currency must be configured for media buys",
                is_complete=currency_count > 0,
                action_url=f"/tenant/{self.tenant_id}/settings#business-rules",
                details=f"{currency_count} currencies configured" if currency_count > 0 else "No currencies configured",
            )
        )

        # 3. Authorized Properties
        stmt = (
            select(func.count()).select_from(AuthorizedProperty).where(AuthorizedProperty.tenant_id == self.tenant_id)
        )
        property_count = session.scalar(stmt) or 0
        tasks.append(
            SetupTask(
                key="authorized_properties",
                name="Authorized Properties",
                description="Configure properties with adagents.json for verification",
                is_complete=property_count > 0,
                action_url=f"/tenant/{self.tenant_id}/authorized-properties",
                details=f"{property_count} properties configured" if property_count > 0 else "No properties configured",
            )
        )

        # 4. Inventory Synced (adapter-specific behavior)
        # Check if tenant has synced inventory from ad server
        # - None: No adapter selected, must configure ad server first (incomplete)
        # - GAM: Requires sync from Google Ad Manager (checks GAMInventory table)
        # - Mock: Has built-in inventory (no sync required)
        # - Kevel/Triton: Check adapter documentation for inventory requirements
        if tenant.ad_server is None or tenant.ad_server == "":
            # No ad server configured - cannot proceed with inventory setup
            tasks.append(
                SetupTask(
                    key="inventory_synced",
                    name="Inventory Sync",
                    description="Configure ad server before syncing inventory",
                    is_complete=False,
                    action_url=f"/tenant/{self.tenant_id}/settings#adserver",
                    details="Ad server must be configured before inventory can be synced",
                )
            )
        elif tenant.ad_server == "google_ad_manager":
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
                    action_url=f"/tenant/{self.tenant_id}/settings#inventory",
                    details=inventory_details,
                )
            )
        elif tenant.ad_server == "mock":
            # Mock adapter has built-in inventory, always complete
            tasks.append(
                SetupTask(
                    key="inventory_synced",
                    name="Inventory Sync",
                    description="Mock adapter has built-in inventory (no sync required)",
                    is_complete=True,
                    action_url=None,
                    details="Mock adapter provides built-in mock inventory automatically",
                )
            )
        elif tenant.ad_server in ["kevel", "triton"]:
            # Kevel and Triton adapters - mark as complete (inventory configured per product)
            # These adapters configure inventory targeting at the product level, not via global sync
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
            # Unknown adapter - show as complete but with note to verify
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

        # 5. Products Created
        stmt = select(func.count()).select_from(Product).where(Product.tenant_id == self.tenant_id)
        product_count = session.scalar(stmt) or 0
        tasks.append(
            SetupTask(
                key="products_created",
                name="Products",
                description="Create at least one advertising product",
                is_complete=product_count > 0,
                action_url=f"/tenant/{self.tenant_id}/products",
                details=f"{product_count} products created" if product_count > 0 else "No products created",
            )
        )

        # 6. Principals Created
        stmt = select(func.count()).select_from(Principal).where(Principal.tenant_id == self.tenant_id)
        principal_count = session.scalar(stmt) or 0
        tasks.append(
            SetupTask(
                key="principals_created",
                name="Advertisers (Principals)",
                description="Create principals for advertisers who will buy inventory",
                is_complete=principal_count > 0,
                action_url=f"/tenant/{self.tenant_id}/settings#advertisers",
                details=(
                    f"{principal_count} advertisers configured" if principal_count > 0 else "No advertisers configured"
                ),
            )
        )

        # 7. Access Control Configured
        has_domains = bool(tenant.authorized_domains and len(tenant.authorized_domains) > 0)
        has_emails = bool(tenant.authorized_emails and len(tenant.authorized_emails) > 0)
        access_control_configured = bool(has_domains or has_emails)

        details = []
        if has_domains and tenant.authorized_domains:
            details.append(f"{len(tenant.authorized_domains)} domain(s)")
        if has_emails and tenant.authorized_emails:
            details.append(f"{len(tenant.authorized_emails)} email(s)")

        tasks.append(
            SetupTask(
                key="access_control",
                name="Access Control",
                description="Configure who can access this tenant (domains or emails)",
                is_complete=access_control_configured,
                action_url=f"/tenant/{self.tenant_id}/settings#account",
                details=(
                    ", ".join(details) if details else "No access control configured - only super admins can access"
                ),
            )
        )

        return tasks

    def _check_recommended_tasks(self, session, tenant: Tenant) -> list[SetupTask]:
        """Check recommended tasks for better experience."""
        tasks = []

        # 1. Creative Approval Guidelines
        policy_settings = tenant.policy_settings or {}
        has_approval_config = tenant.human_review_required is not None or tenant.auto_approve_format_ids
        tasks.append(
            SetupTask(
                key="creative_approval_guidelines",
                name="Creative Approval Guidelines",
                description="Configure auto-approval rules and manual review settings",
                is_complete=has_approval_config,
                action_url=f"/tenant/{self.tenant_id}/settings#business-rules",
                details="Approval workflow configured" if has_approval_config else "Using default approval settings",
            )
        )

        # 2. Naming Conventions
        has_custom_naming = bool(tenant.order_name_template or tenant.line_item_name_template)
        tasks.append(
            SetupTask(
                key="naming_conventions",
                name="Naming Conventions",
                description="Customize order and line item naming templates",
                is_complete=has_custom_naming,
                action_url=f"/tenant/{self.tenant_id}/settings#business-rules",
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
                action_url=f"/tenant/{self.tenant_id}/settings#business-rules",
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
                action_url=f"/tenant/{self.tenant_id}/targeting",
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
                action_url=f"/tenant/{self.tenant_id}/settings#integrations",
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
                action_url=f"/tenant/{self.tenant_id}/settings#account",
                details=f"Using {virtual_host}" if has_custom_domain else "Using default subdomain",
            )
        )

        return tasks

    def _check_optional_tasks(self, session, tenant: Tenant) -> list[SetupTask]:
        """Check optional enhancement tasks."""
        tasks = []

        # 1. Signals Discovery Agent
        signals_enabled = tenant.enable_axe_signals or False
        tasks.append(
            SetupTask(
                key="signals_agent",
                name="Signals Discovery Agent",
                description="Enable AXE signals for advanced targeting",
                is_complete=signals_enabled,
                action_url=f"/tenant/{self.tenant_id}/settings#integrations",
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
                action_url=f"/tenant/{self.tenant_id}/settings#integrations",
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
                action_url=f"/tenant/{self.tenant_id}/settings#business-rules",
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
        gam_inventory_count: int,
        product_count: int,
        principal_count: int,
    ) -> list[SetupTask]:
        """Build critical tasks from pre-fetched data (no session queries)."""
        tasks = []

        # 1. Ad Server Configuration
        ad_server_selected = tenant.ad_server is not None and tenant.ad_server != ""
        ad_server_fully_configured = False
        config_details = "No ad server configured"

        if ad_server_selected:
            if tenant.ad_server == "google_ad_manager":
                ad_server_fully_configured = True
                config_details = "GAM configured - Test connection to verify"
            elif tenant.ad_server == "mock":
                ad_server_fully_configured = True
                config_details = "Mock adapter configured - Ready for testing"
            elif tenant.ad_server in ["kevel", "triton"]:
                ad_server_fully_configured = True
                config_details = f"{tenant.ad_server} adapter configured"
            else:
                ad_server_fully_configured = True
                config_details = f"{tenant.ad_server} adapter - verify configuration"

        tasks.append(
            SetupTask(
                key="ad_server_connected",
                name="⚠️ Ad Server Configuration",
                description="BLOCKER: Configure and test ad server connection before proceeding with other setup",
                is_complete=ad_server_fully_configured,
                action_url=f"/tenant/{self.tenant_id}/settings#adserver",
                details=config_details,
            )
        )

        # 2. Currency Limits
        tasks.append(
            SetupTask(
                key="currency_limits",
                name="Currency Configuration",
                description="At least one currency must be configured for media buys",
                is_complete=currency_count > 0,
                action_url=f"/tenant/{self.tenant_id}/settings#business-rules",
                details=f"{currency_count} currencies configured" if currency_count > 0 else "No currencies configured",
            )
        )

        # 3. Authorized Properties
        tasks.append(
            SetupTask(
                key="authorized_properties",
                name="Authorized Properties",
                description="Configure properties with adagents.json for verification",
                is_complete=property_count > 0,
                action_url=f"/tenant/{self.tenant_id}/authorized-properties",
                details=f"{property_count} properties configured" if property_count > 0 else "No properties configured",
            )
        )

        # 4. Inventory Synced
        if tenant.ad_server is None or tenant.ad_server == "":
            tasks.append(
                SetupTask(
                    key="inventory_synced",
                    name="Inventory Sync",
                    description="Configure ad server before syncing inventory",
                    is_complete=False,
                    action_url=f"/tenant/{self.tenant_id}/settings#adserver",
                    details="Ad server must be configured before inventory can be synced",
                )
            )
        elif tenant.ad_server == "google_ad_manager":
            inventory_synced = gam_inventory_count > 0
            tasks.append(
                SetupTask(
                    key="inventory_synced",
                    name="Inventory Sync",
                    description="Sync ad units and placements from ad server",
                    is_complete=inventory_synced,
                    action_url=f"/tenant/{self.tenant_id}/settings#inventory",
                    details=(
                        f"{gam_inventory_count:,} inventory items synced"
                        if inventory_synced
                        else "No inventory synced from ad server"
                    ),
                )
            )
        elif tenant.ad_server == "mock":
            tasks.append(
                SetupTask(
                    key="inventory_synced",
                    name="Inventory Sync",
                    description="Mock adapter has built-in inventory (no sync required)",
                    is_complete=True,
                    action_url=None,
                    details="Mock adapter provides built-in mock inventory automatically",
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

        # 5. Products Created
        tasks.append(
            SetupTask(
                key="products_created",
                name="Products",
                description="Create at least one advertising product",
                is_complete=product_count > 0,
                action_url=f"/tenant/{self.tenant_id}/products",
                details=f"{product_count} products created" if product_count > 0 else "No products created",
            )
        )

        # 6. Principals Created
        tasks.append(
            SetupTask(
                key="principals_created",
                name="Advertisers (Principals)",
                description="Create principals for advertisers who will buy inventory",
                is_complete=principal_count > 0,
                action_url=f"/tenant/{self.tenant_id}/settings#advertisers",
                details=(
                    f"{principal_count} advertisers configured" if principal_count > 0 else "No advertisers configured"
                ),
            )
        )

        # 7. Access Control
        has_domains = bool(tenant.authorized_domains and len(tenant.authorized_domains) > 0)
        has_emails = bool(tenant.authorized_emails and len(tenant.authorized_emails) > 0)
        access_control_configured = bool(has_domains or has_emails)

        details = []
        if has_domains and tenant.authorized_domains:
            details.append(f"{len(tenant.authorized_domains)} domain(s)")
        if has_emails and tenant.authorized_emails:
            details.append(f"{len(tenant.authorized_emails)} email(s)")

        tasks.append(
            SetupTask(
                key="access_control",
                name="Access Control",
                description="Configure who can access this tenant (domains or emails)",
                is_complete=access_control_configured,
                action_url=f"/tenant/{self.tenant_id}/settings#account",
                details=(
                    ", ".join(details) if details else "No access control configured - only super admins can access"
                ),
            )
        )

        return tasks

    def _build_recommended_tasks(self, tenant: Tenant, budget_limit_count: int, currency_count: int) -> list[SetupTask]:
        """Build recommended tasks from pre-fetched data (no session queries)."""
        tasks = []

        # 1. Creative Approval Guidelines
        has_approval_config = tenant.human_review_required is not None or tenant.auto_approve_format_ids
        tasks.append(
            SetupTask(
                key="creative_approval_guidelines",
                name="Creative Approval Guidelines",
                description="Configure auto-approval rules and manual review settings",
                is_complete=has_approval_config,
                action_url=f"/tenant/{self.tenant_id}/settings#business-rules",
                details="Approval workflow configured" if has_approval_config else "Using default approval settings",
            )
        )

        # 2. Naming Conventions
        has_custom_naming = bool(tenant.order_name_template or tenant.line_item_name_template)
        tasks.append(
            SetupTask(
                key="naming_conventions",
                name="Naming Conventions",
                description="Customize order and line item naming templates",
                is_complete=has_custom_naming,
                action_url=f"/tenant/{self.tenant_id}/settings#business-rules",
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
                action_url=f"/tenant/{self.tenant_id}/settings#business-rules",
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
                action_url=f"/tenant/{self.tenant_id}/targeting",
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
                action_url=f"/tenant/{self.tenant_id}/settings#integrations",
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
                action_url=f"/tenant/{self.tenant_id}/settings#account",
                details=f"Using {tenant.virtual_host}" if has_custom_domain else "Using default subdomain",
            )
        )

        return tasks

    def _build_optional_tasks(self, tenant: Tenant, currency_count: int) -> list[SetupTask]:
        """Build optional tasks from pre-fetched data (no session queries)."""
        tasks = []

        # 1. Signals Discovery Agent
        signals_enabled = tenant.enable_axe_signals or False
        tasks.append(
            SetupTask(
                key="signals_agent",
                name="Signals Discovery Agent",
                description="Enable AXE signals for advanced targeting",
                is_complete=signals_enabled,
                action_url=f"/tenant/{self.tenant_id}/settings#integrations",
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
                action_url=f"/tenant/{self.tenant_id}/settings#integrations",
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
                action_url=f"/tenant/{self.tenant_id}/settings#business-rules",
                details=(
                    f"{currency_count} currencies supported" if multiple_currencies else "Only 1 currency configured"
                ),
            )
        )

        return tasks

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

    Args:
        tenant_id: Tenant ID to validate

    Raises:
        SetupIncompleteError: If critical setup tasks are incomplete
    """
    incomplete = get_incomplete_critical_tasks(tenant_id)
    if incomplete:
        task_names = ", ".join(task["name"] for task in incomplete)
        raise SetupIncompleteError(
            f"Complete required setup tasks before creating orders: {task_names}", missing_tasks=incomplete
        )
