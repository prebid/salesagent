from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

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


class CreativeEngineAdapter(ABC):
    """Abstract base class for creative engine adapters."""

    @abstractmethod
    def process_assets(self, media_buy_id: str, assets: list[dict[str, Any]]) -> list[AssetStatus]:
        pass


class AdServerAdapter(ABC):
    """Abstract base class for ad server adapters."""

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
