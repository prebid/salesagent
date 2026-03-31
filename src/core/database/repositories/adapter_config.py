"""AdapterConfig repository — tenant-scoped access to adapter configuration.

Centralizes all AdapterConfig database access. Handles GAM config construction
(both OAuth and service account auth), targeting config, and naming templates.

Decoupled from TenantConfigRepository because the 1:1 tenant-adapter
relationship will become 1:N when multi-adapter support is added.

Core invariant: every query includes tenant_id in the WHERE clause.

Design rules (PR #1171 review):
  1. Fail loudly: get_by_tenant() raises on missing data; find_by_tenant() for
     cases where absence is normal.
  2. Separate query from logic: logic methods accept pre-loaded AdapterConfig,
     never query the DB internally.
  3. No bare except: callers handle specific exceptions.

Introduced in PR #1163, redesigned in PR #1171.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import AdapterConfig


class TenantNotConfiguredError(Exception):
    """Raised when a tenant has no AdapterConfig row.

    This indicates a setup/configuration issue — the tenant exists but has
    not been configured with an ad server adapter yet.
    """

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        super().__init__(f"No adapter configuration found for tenant {tenant_id!r}")


class AdapterConfigRepository:
    """Tenant-scoped access for adapter configuration.

    All queries filter by tenant_id automatically. Callers cannot bypass
    tenant isolation.

    Query methods: get_by_tenant (raises), find_by_tenant (Optional).
    Logic methods: accept pre-loaded AdapterConfig, no DB access.
    Write methods: raise on missing config.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ------------------------------------------------------------------
    # Query methods (touch the DB)
    # ------------------------------------------------------------------

    def get_by_tenant(self) -> AdapterConfig:
        """Get the adapter configuration for the tenant.

        Raises:
            TenantNotConfiguredError: If no AdapterConfig row exists for the tenant.
        """
        config = self.find_by_tenant()
        if config is None:
            raise TenantNotConfiguredError(self._tenant_id)
        return config

    def find_by_tenant(self) -> AdapterConfig | None:
        """Get the adapter configuration for the tenant, or None if not configured.

        Use this when absence is a normal case (e.g., checking whether a tenant
        has been set up yet, or defaulting to mock adapter).
        """
        stmt = select(AdapterConfig).filter_by(tenant_id=self._tenant_id)
        return self._session.scalars(stmt).first()

    def get_adapter_type(self) -> str | None:
        """Get the adapter type string (e.g., 'google_ad_manager', 'mock'), or None.

        Convenience method — uses find_by_tenant() internally because
        absence is normal (unconfigured tenants default to mock).
        """
        config = self.find_by_tenant()
        return config.adapter_type if config else None

    # ------------------------------------------------------------------
    # Logic methods (pure — accept pre-loaded config, no DB)
    # ------------------------------------------------------------------

    @staticmethod
    def has_gam_credentials(config: AdapterConfig) -> bool:
        """Check if the config has valid GAM credentials (OAuth or service account).

        This is the single source of truth for validation gates. Replaces scattered
        inline checks like ``if not adapter_config.gam_refresh_token``.

        Pure logic — no DB access. Caller must pass a pre-loaded AdapterConfig.
        """
        if config.adapter_type != "google_ad_manager":
            return False
        return bool(config.gam_refresh_token or config.gam_service_account_json)

    @staticmethod
    def get_gam_config(config: AdapterConfig) -> dict[str, Any]:
        """Build GAM config dict suitable for GoogleAdManager / GAMAuthManager.

        Delegates to ``build_gam_config_from_adapter()`` — the canonical builder
        that handles both OAuth and service account auth methods.

        Pure logic — no DB access. Caller must pass a pre-loaded AdapterConfig.

        Raises:
            ValueError: If the config is not a GAM adapter.
        """
        if config.adapter_type != "google_ad_manager":
            raise ValueError(f"Tenant {config.tenant_id!r} is not a GAM adapter (adapter_type={config.adapter_type!r})")

        from src.adapters.gam import build_gam_config_from_adapter

        return build_gam_config_from_adapter(config)

    @staticmethod
    def get_gam_targeting_config(config: AdapterConfig) -> dict[str, Any]:
        """Get AXE targeting keys and custom targeting key mappings.

        Returns dict with: axe_include_key, axe_exclude_key, axe_macro_key,
        custom_targeting_keys. All values may be None/empty.

        Pure logic — no DB access. Caller must pass a pre-loaded AdapterConfig.
        """
        return {
            "axe_include_key": config.axe_include_key,
            "axe_exclude_key": config.axe_exclude_key,
            "axe_macro_key": config.axe_macro_key,
            "custom_targeting_keys": config.custom_targeting_keys or {},
        }

    @staticmethod
    def get_gam_naming_templates(config: AdapterConfig) -> tuple[str | None, str | None]:
        """Get GAM order and line item naming templates.

        Returns:
            (order_name_template, line_item_name_template) — either may be None.

        Pure logic — no DB access. Caller must pass a pre-loaded AdapterConfig.
        """
        return (config.gam_order_name_template, config.gam_line_item_name_template)

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    def update_custom_targeting_keys(self, keys: dict[str, str]) -> None:
        """Update the cached custom targeting key mappings.

        Does not commit — caller (UoW) handles transaction boundary.

        Raises:
            TenantNotConfiguredError: If no AdapterConfig row exists.
        """
        config = self.get_by_tenant()  # raises if missing
        config.custom_targeting_keys = keys
