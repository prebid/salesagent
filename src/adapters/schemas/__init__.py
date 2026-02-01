"""
Adapter schema registry and base classes.

This module provides the schema infrastructure for adapter configurations.
Each adapter declares its schemas and capabilities, registered here.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import BaseConnectionConfig, BaseInventoryConfig, BaseProductConfig

if TYPE_CHECKING:
    from src.adapters.base import AdapterCapabilities

__all__ = [
    "BaseConnectionConfig",
    "BaseProductConfig",
    "BaseInventoryConfig",
    "AdapterSchemas",
    "ADAPTER_SCHEMA_REGISTRY",
    "get_adapter_schemas",
]


@dataclass
class AdapterSchemas:
    """Container for an adapter's schema classes and capabilities."""

    connection_config: type[BaseConnectionConfig]
    product_config: type[BaseProductConfig] | None = None
    inventory_config: type[BaseInventoryConfig] | None = None
    capabilities: "AdapterCapabilities | None" = None


# Registry mapping adapter type -> AdapterSchemas
# Populated by adapter modules on import
ADAPTER_SCHEMA_REGISTRY: dict[str, AdapterSchemas] = {}
_REGISTRY_INITIALIZED = False


def register_adapter_schemas(
    adapter_type: str,
    connection_config: type[BaseConnectionConfig],
    product_config: type[BaseProductConfig] | None = None,
    inventory_config: type[BaseInventoryConfig] | None = None,
    capabilities: "AdapterCapabilities | None" = None,
) -> None:
    """Register schemas for an adapter type.

    Args:
        adapter_type: The adapter type identifier (e.g., "mock", "gam")
        connection_config: Schema class for connection configuration
        product_config: Optional schema class for product configuration
        inventory_config: Optional schema class for inventory configuration
        capabilities: Optional capabilities declaration
    """
    ADAPTER_SCHEMA_REGISTRY[adapter_type] = AdapterSchemas(
        connection_config=connection_config,
        product_config=product_config,
        inventory_config=inventory_config,
        capabilities=capabilities,
    )


def get_adapter_schemas(adapter_type: str) -> AdapterSchemas | None:
    """Get schemas for an adapter type.

    Lazily registers schemas on first access to avoid circular imports.

    Args:
        adapter_type: The adapter type identifier

    Returns:
        AdapterSchemas if registered, None otherwise
    """
    global _REGISTRY_INITIALIZED
    # Lazy registration on first access
    if not _REGISTRY_INITIALIZED:
        _register_all_adapters()
        _REGISTRY_INITIALIZED = True

    return ADAPTER_SCHEMA_REGISTRY.get(adapter_type)


def _register_all_adapters() -> None:
    """Register all adapter schemas.

    Called lazily on first access to avoid circular imports.
    """
    # Import capabilities from base
    from pydantic import Field

    from src.adapters.base import AdapterCapabilities

    # Mock adapter
    from src.adapters.schemas.base import BaseConnectionConfig, BaseProductConfig

    # Define Mock schemas inline to avoid circular imports
    class MockConnectionConfig(BaseConnectionConfig):
        dry_run: bool = Field(default=False, description="When true, simulates operations without persisting state")

    class MockProductConfig(BaseProductConfig):
        daily_impressions: int = Field(default=10000, ge=0)
        fill_rate: float = Field(default=0.85, ge=0.0, le=1.0)
        ctr: float = Field(default=0.02, ge=0.0, le=1.0)
        viewability: float = Field(default=0.65, ge=0.0, le=1.0)
        scenario: str = Field(default="normal")

    MOCK_CAPABILITIES = AdapterCapabilities(
        supports_inventory_sync=False,
        supports_inventory_profiles=False,
        inventory_entity_label="Mock Items",
        supports_custom_targeting=False,
        supports_geo_targeting=True,
        supports_dynamic_products=False,
        supported_pricing_models=["cpm", "vcpm", "cpcv", "cpp", "cpc", "cpv", "flat_rate"],
        supports_webhooks=False,
        supports_realtime_reporting=False,
    )

    register_adapter_schemas(
        adapter_type="mock",
        connection_config=MockConnectionConfig,
        product_config=MockProductConfig,
        capabilities=MOCK_CAPABILITIES,
    )

    # GAM adapter
    class GAMConnectionConfig(BaseConnectionConfig):
        network_code: str | None = Field(None, description="GAM network code")
        auth_method: str = Field("oauth", description="Authentication method: oauth or service_account")
        refresh_token: str | None = Field(None, exclude=True)
        service_account_json: str | None = Field(None, exclude=True)
        service_account_email: str | None = Field(None)
        network_currency: str | None = Field(None)
        secondary_currencies: list[str] | None = Field(None)
        network_timezone: str | None = Field(None)
        trafficker_id: str | None = Field(None)
        manual_approval_required: bool = Field(False)
        order_name_template: str | None = Field(None)
        line_item_name_template: str | None = Field(None)

    class GAMProductConfig(BaseProductConfig):
        targeted_ad_unit_ids: list[str] = Field(default_factory=list)
        targeted_placement_ids: list[str] = Field(default_factory=list)
        include_descendants: bool = Field(True)
        order_name_template: str = Field("AdCP-{po_number}-{product_name}-{timestamp}")
        creative_rotation_type: str = Field("EVEN")
        delivery_rate_type: str = Field("EVENLY")
        allow_overbook: bool = Field(False)

    GAM_CAPABILITIES = AdapterCapabilities(
        supports_inventory_sync=True,
        supports_inventory_profiles=True,
        inventory_entity_label="Ad Units & Placements",
        supports_custom_targeting=True,
        supports_geo_targeting=True,
        supports_dynamic_products=True,
        supported_pricing_models=["cpm", "vcpm", "cpc", "flat_rate"],
        supports_webhooks=False,
        supports_realtime_reporting=True,
    )

    register_adapter_schemas(
        adapter_type="google_ad_manager",
        connection_config=GAMConnectionConfig,
        product_config=GAMProductConfig,
        capabilities=GAM_CAPABILITIES,
    )

    # Kevel adapter
    class KevelConnectionConfig(BaseConnectionConfig):
        network_id: str | None = Field(None)
        api_key: str | None = Field(None, exclude=True)
        manual_approval_required: bool = Field(False)

    KEVEL_CAPABILITIES = AdapterCapabilities(
        supports_inventory_sync=False,
        supports_inventory_profiles=False,
        inventory_entity_label="Sites",
        supports_custom_targeting=True,
        supports_geo_targeting=True,
        supports_dynamic_products=False,
        supported_pricing_models=["cpm", "cpc", "flat_rate"],
        supports_webhooks=True,
        supports_realtime_reporting=False,
    )

    register_adapter_schemas(
        adapter_type="kevel",
        connection_config=KevelConnectionConfig,
        capabilities=KEVEL_CAPABILITIES,
    )

    # Triton adapter
    class TritonConnectionConfig(BaseConnectionConfig):
        station_id: str | None = Field(None)
        api_key: str | None = Field(None, exclude=True)

    TRITON_CAPABILITIES = AdapterCapabilities(
        supports_inventory_sync=False,
        supports_inventory_profiles=False,
        inventory_entity_label="Stations",
        supports_custom_targeting=False,
        supports_geo_targeting=True,
        supports_dynamic_products=False,
        supported_pricing_models=["cpm", "flat_rate"],
        supports_webhooks=False,
        supports_realtime_reporting=False,
    )

    register_adapter_schemas(
        adapter_type="triton_digital",
        connection_config=TritonConnectionConfig,
        capabilities=TRITON_CAPABILITIES,
    )
