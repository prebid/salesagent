"""AdapterConfig repository — tenant-scoped access to adapter configuration.

Centralizes all AdapterConfig database access. Handles GAM config construction
(both OAuth and service account auth), targeting config, and naming templates.

Decoupled from TenantConfigRepository because the 1:1 tenant-adapter
relationship will become 1:N when multi-adapter support is added.

Core invariant: every query includes tenant_id in the WHERE clause.

beads: salesagent-zj9 (epic), salesagent-g3m (creation)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import AdapterConfig


class AdapterConfigRepository:
    """Tenant-scoped read access for adapter configuration.

    All queries filter by tenant_id automatically. Callers cannot bypass
    tenant isolation.

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
    # Core read methods
    # ------------------------------------------------------------------

    def get_by_tenant(self) -> AdapterConfig | None:
        """Get the adapter configuration for the tenant, or None if not configured."""
        stmt = select(AdapterConfig).filter_by(tenant_id=self._tenant_id)
        return self._session.scalars(stmt).first()

    def get_adapter_type(self) -> str | None:
        """Get the adapter type string (e.g., 'google_ad_manager', 'mock'), or None."""
        config = self.get_by_tenant()
        return config.adapter_type if config else None

    def has_gam_credentials(self) -> bool:
        """Check if the tenant has valid GAM credentials (OAuth or service account).

        This is the single source of truth for validation gates. Replaces scattered
        inline checks like ``if not adapter_config.gam_refresh_token``.
        """
        config = self.get_by_tenant()
        if not config or config.adapter_type != "google_ad_manager":
            return False
        return bool(config.gam_refresh_token or config.gam_service_account_json)

    # ------------------------------------------------------------------
    # GAM config construction
    # ------------------------------------------------------------------

    def get_gam_config(self) -> dict[str, Any]:
        """Build GAM config dict suitable for GoogleAdManager / GAMAuthManager.

        Delegates to ``build_gam_config_from_adapter()`` — the canonical builder
        that handles both OAuth and service account auth methods.

        Raises:
            ValueError: If no AdapterConfig exists or it's not a GAM adapter.
        """
        config = self.get_by_tenant()
        if not config or config.adapter_type != "google_ad_manager":
            raise ValueError(
                f"Tenant {self._tenant_id} is not a GAM adapter "
                f"(adapter_type={config.adapter_type if config else None})"
            )

        from src.adapters.gam import build_gam_config_from_adapter

        return build_gam_config_from_adapter(config)

    # ------------------------------------------------------------------
    # GAM targeting and naming config (eliminates adapter→DB dependency)
    # ------------------------------------------------------------------

    def get_gam_targeting_config(self) -> dict[str, Any]:
        """Get AXE targeting keys and custom targeting key mappings.

        Returns dict with: axe_include_key, axe_exclude_key, axe_macro_key,
        custom_targeting_keys. All values may be None/empty.
        """
        config = self.get_by_tenant()
        if not config:
            return {
                "axe_include_key": None,
                "axe_exclude_key": None,
                "axe_macro_key": None,
                "custom_targeting_keys": {},
            }
        return {
            "axe_include_key": config.axe_include_key,
            "axe_exclude_key": config.axe_exclude_key,
            "axe_macro_key": config.axe_macro_key,
            "custom_targeting_keys": config.custom_targeting_keys or {},
        }

    def get_gam_naming_templates(self) -> tuple[str | None, str | None]:
        """Get GAM order and line item naming templates.

        Returns:
            (order_name_template, line_item_name_template) — either may be None.
        """
        config = self.get_by_tenant()
        if not config:
            return (None, None)
        return (config.gam_order_name_template, config.gam_line_item_name_template)

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    def update_custom_targeting_keys(self, keys: dict[str, str]) -> None:
        """Update the cached custom targeting key mappings.

        Does not commit — caller (UoW) handles transaction boundary.
        """
        config = self.get_by_tenant()
        if config:
            config.custom_targeting_keys = keys
