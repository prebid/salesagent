from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from src.core.schemas import Targeting

from pydantic import BaseModel, ConfigDict, Field
from rich.console import Console

from src.core.audit_logger import get_audit_logger
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AssetStatus,
    CheckMediaBuyStatusResponse,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    MediaPackage,
    PackagePerformance,
    Principal,
    ReportingPeriod,
    UpdateMediaBuyResponse,
)


@dataclass
class TargetingCapabilities:
    """Targeting capabilities supported by an adapter.

    Maps to AdCP GetAdcpCapabilitiesResponse.media_buy.execution.targeting structure.
    """

    # Geographic targeting
    geo_countries: bool = False
    geo_regions: bool = False

    # Metro/DMA targeting
    nielsen_dma: bool = False  # US Nielsen DMAs
    eurostat_nuts2: bool = False  # EU NUTS2 regions
    uk_itl1: bool = False  # UK ITL1 regions
    uk_itl2: bool = False  # UK ITL2 regions

    # Postal code targeting
    us_zip: bool = False
    us_zip_plus_four: bool = False
    ca_fsa: bool = False  # Canadian FSA
    ca_full: bool = False  # Full Canadian postal code
    gb_outward: bool = False  # UK outward code (first part)
    gb_full: bool = False  # Full UK postcode
    de_plz: bool = False  # German PLZ
    fr_code_postal: bool = False  # French postal code
    au_postcode: bool = False  # Australian postcode

    # Maps from AdCP enum value → dataclass field name.
    _METRO_FIELDS: ClassVar[tuple[str, ...]] = (
        "nielsen_dma",
        "eurostat_nuts2",
        "uk_itl1",
        "uk_itl2",
    )
    _POSTAL_FIELDS: ClassVar[tuple[str, ...]] = (
        "us_zip",
        "us_zip_plus_four",
        "gb_outward",
        "gb_full",
        "ca_fsa",
        "ca_full",
        "de_plz",
        "fr_code_postal",
        "au_postcode",
    )

    def validate_geo_systems(self, targeting: Targeting) -> list[str]:
        """Validate that targeting geo systems are supported by this adapter.

        Checks both include and exclude fields for geo_metros and geo_postal_areas.
        Returns list of errors naming the unsupported system and supported alternatives.
        """
        from src.core.validation_helpers import resolve_enum_value

        errors: list[str] = []

        # Collect all metro items from include + exclude
        metros: list[Any] = []
        if targeting.geo_metros:
            metros.extend(targeting.geo_metros)
        if targeting.geo_metros_exclude:
            metros.extend(targeting.geo_metros_exclude)

        if metros:
            supported = [f for f in self._METRO_FIELDS if getattr(self, f)]
            for metro in metros:
                system = resolve_enum_value(metro.system)
                if not getattr(self, system, False):
                    alt = ", ".join(supported) if supported else "none"
                    errors.append(f"Unsupported metro system '{system}'. This adapter supports: {alt}")

        # Collect all postal items from include + exclude
        postals: list[Any] = []
        if targeting.geo_postal_areas:
            postals.extend(targeting.geo_postal_areas)
        if targeting.geo_postal_areas_exclude:
            postals.extend(targeting.geo_postal_areas_exclude)

        if postals:
            supported = [f for f in self._POSTAL_FIELDS if getattr(self, f)]
            for area in postals:
                system = resolve_enum_value(area.system)
                if not getattr(self, system, False):
                    alt = ", ".join(supported) if supported else "none"
                    errors.append(f"Unsupported postal system '{system}'. This adapter supports: {alt}")

        return errors


@dataclass
class AdapterCapabilities:
    """UI and feature capabilities declared by an adapter.

    Controls which UI sections are shown and what features are available.
    Used by admin UI to show/hide relevant configuration sections.
    """

    # Inventory management
    supports_inventory_sync: bool = False  # Can sync inventory from ad server
    supports_inventory_profiles: bool = False  # Supports inventory profile configuration
    inventory_entity_label: str = "Items"  # UI label for inventory entities (e.g., "Zones", "Ad Units")

    # Targeting
    supports_custom_targeting: bool = False  # Supports custom key-value targeting
    supports_geo_targeting: bool = True  # Supports geographic targeting

    # Product configuration
    supports_dynamic_products: bool = False  # Supports AI-driven product configuration

    # Pricing (None means all pricing models supported)
    supported_pricing_models: list[str] | None = None

    # Reporting and webhooks
    supports_webhooks: bool = False  # Supports webhook notifications
    supports_realtime_reporting: bool = False  # Supports real-time delivery reporting


class BaseConnectionConfig(BaseModel):
    """Base schema for adapter connection configuration."""

    model_config = ConfigDict(extra="forbid")

    manual_approval_required: bool = Field(
        default=False,
        description="Require human approval for operations like create_media_buy",
    )


class BaseProductConfig(BaseModel):
    """Base schema for product-level adapter configuration."""

    model_config = ConfigDict(extra="forbid")


class CreativeEngineAdapter(ABC):
    """Abstract base class for creative engine adapters."""

    @abstractmethod
    def process_assets(self, media_buy_id: str, assets: list[dict[str, Any]]) -> list[AssetStatus]:
        pass


class AdServerAdapter(ABC):
    """Abstract base class for ad server adapters."""

    # Default advertising channels supported by this adapter
    # Subclasses should override with their supported channels
    default_channels: list[str] = []

    # Adapter capabilities - override in subclasses
    capabilities: AdapterCapabilities = AdapterCapabilities()

    # Connection config schema - override in subclasses
    connection_config_class: type[BaseConnectionConfig] | None = BaseConnectionConfig

    # Product config schema - override in subclasses (optional)
    product_config_class: type[BaseProductConfig] | None = None

    def __init__(
        self,
        config: dict[str, Any],
        principal: Principal,
        dry_run: bool = False,
        creative_engine: CreativeEngineAdapter | None = None,
        tenant_id: str | None = None,
    ):
        self.config = config
        self.principal = principal
        self.principal_id = principal.principal_id  # For backward compatibility
        self.dry_run = dry_run
        self.creative_engine = creative_engine
        self.tenant_id = tenant_id
        self.console = Console()

        # Set adapter_principal_id after initialization when adapter_name is available
        if hasattr(self.__class__, "adapter_name"):
            self.adapter_principal_id = principal.get_adapter_id(self.__class__.adapter_name)
        else:
            self.adapter_principal_id = None

        # Initialize audit logger with adapter name and tenant_id
        adapter_name = getattr(self.__class__, "adapter_name", self.__class__.__name__)
        self.audit_logger = get_audit_logger(adapter_name, tenant_id)

        # Manual approval mode - requires human approval for all operations
        self.manual_approval_required = config.get("manual_approval_required", False)
        self.manual_approval_operations = set(
            config.get("manual_approval_operations", ["create_media_buy", "update_media_buy", "add_creative_assets"])
        )

    def log(self, message: str, dry_run_prefix: bool = True):
        """Log a message, with optional dry-run prefix."""
        if self.dry_run and dry_run_prefix:
            self.console.print(f"[dim](dry-run)[/dim] {message}")
        else:
            self.console.print(message)

    def get_supported_pricing_models(self) -> set[str]:
        """Return set of pricing models this adapter supports (AdCP PR #88).

        Default implementation supports only CPM. Override in subclasses.

        Returns:
            Set of pricing model strings: {"cpm", "cpcv", "cpp", "cpc", "cpv", "flat_rate"}
        """
        return {"cpm"}

    def get_targeting_capabilities(self) -> TargetingCapabilities:
        """Return targeting capabilities this adapter supports.

        Default implementation returns minimal capabilities (geo country only).
        Override in subclasses with actual adapter capabilities.

        Returns:
            TargetingCapabilities describing what targeting is supported
        """
        return TargetingCapabilities(geo_countries=True)

    @abstractmethod
    def create_media_buy(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> CreateMediaBuyResponse:
        """Creates a new media buy on the ad server from selected packages.

        Args:
            request: Full create media buy request
            packages: Simplified package models for adapter
            start_time: Campaign start time
            end_time: Campaign end time
            package_pricing_info: Optional validated pricing information per package (AdCP PR #88)
                Maps package_id → {pricing_model, rate, currency, is_fixed, bid_price}

        Returns:
            CreateMediaBuyResponse with media buy details
        """
        pass

    @abstractmethod
    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """Adds creative assets to an existing media buy."""
        pass

    @abstractmethod
    def associate_creatives(self, line_item_ids: list[str], platform_creative_ids: list[str]) -> list[dict[str, Any]]:
        """Associate already-uploaded creatives with line items.

        This is used when buyer provides creative_ids in create_media_buy request,
        indicating they've already synced creatives and want them associated immediately.

        Args:
            line_item_ids: Platform-specific line item IDs
            platform_creative_ids: Platform-specific creative IDs (already uploaded via sync_creatives)

        Returns:
            List of association results with status for each combination
            Example: [{"line_item_id": "123", "creative_id": "456", "status": "success"}]
        """
        pass

    @abstractmethod
    def check_media_buy_status(self, media_buy_id: str, today: datetime) -> CheckMediaBuyStatusResponse:
        """Checks the status of a media buy on the ad server."""
        pass

    @abstractmethod
    def get_media_buy_delivery(
        self, media_buy_id: str, date_range: ReportingPeriod, today: datetime
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Gets delivery data for a media buy."""
        pass

    @abstractmethod
    def update_media_buy_performance_index(
        self, media_buy_id: str, package_performance: list[PackagePerformance]
    ) -> bool:
        """Updates the performance index for packages in a media buy."""
        pass

    @abstractmethod
    def update_media_buy(
        self,
        media_buy_id: str,
        buyer_ref: str,
        action: str,
        package_id: str | None,
        budget: int | None,
        today: datetime,
    ) -> UpdateMediaBuyResponse:
        """Updates a media buy with a specific action."""
        pass

    def get_config_ui_endpoint(self) -> str | None:
        """
        Returns the endpoint path for this adapter's configuration UI.
        If None, the adapter doesn't provide a custom UI.

        Example: "/adapters/gam/config"
        """
        return None

    def register_ui_routes(self, app):
        """
        Register Flask routes for this adapter's configuration UI.
        Called during app initialization if the adapter provides UI.

        Example:
        @app.route('/adapters/gam/config/<tenant_id>/<product_id>')
        def gam_product_config(tenant_id, product_id):
            return render_template('gam_config.html', ...)
        """
        pass

    def validate_product_config(self, config: dict[str, Any]) -> tuple[bool, str | None]:
        """
        Validate product-specific configuration for this adapter.
        Returns (is_valid, error_message)
        """
        return True, None

    async def get_available_inventory(self) -> dict[str, Any]:
        """
        Fetch available inventory from the ad server for AI-driven configuration.
        Returns a dictionary with:
        - placements: List of available ad placements with their capabilities
        - ad_units: List of ad units/pages where ads can be shown
        - targeting_options: Available targeting dimensions and values
        - creative_specs: Supported creative formats and specifications
        - properties: Any additional properties specific to the ad server

        This is used by the AI product configuration service to understand
        what's available when auto-configuring products.
        """
        # Default implementation returns empty inventory
        return {"placements": [], "ad_units": [], "targeting_options": {}, "creative_specs": [], "properties": {}}

    def get_creative_formats(self) -> list[dict[str, Any]]:
        """Return creative formats provided by this adapter.

        Override in adapters that act as both sales and creative agents.
        Returns format definitions that will be included in list_creative_formats.

        Each format dict should match AdCP Format schema:
        {
            "format_id": {"id": "cube_3d", "agent_url": "..."},
            "name": "3D Cube Gallery",
            "type": "display",
            "assets": [
                {"item_type": "individual", "asset_id": "front_image", "asset_type": "image", "required": True},
                ...
            ],
            "description": "6-sided rotating cube with images",
        }

        Returns:
            List of format dictionaries (empty by default)
        """
        return []
