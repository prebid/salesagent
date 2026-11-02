from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

if TYPE_CHECKING:
    from src.core.schemas import Snapshot, Targeting

from adcp.types import BrandReference
from adcp.types.aliases import Package as ResponsePackage
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from rich.console import Console

from src.core.audit_logger import get_audit_logger
from src.core.enum_helpers import enum_value
from src.core.errors.codes import ErrorCode
from src.core.errors.details import ErrorProblem
from src.core.exceptions import AdCPConfigurationError
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AffectedPackage,
    AssetStatus,
    CheckMediaBuyStatusResponse,
    CreateMediaBuyRequest,
    MediaPackage,
    Principal,
    PushNotificationConfig,
    ReportingPeriod,
)
from src.core.validation_helpers import package_field_path

# Return type of AdServerAdapter._require_config — preserves the caller's value type.
_ConfigT = TypeVar("_ConfigT")


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
    ch_plz: bool = False  # Swiss PLZ
    at_plz: bool = False  # Austrian PLZ
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
        "ch_plz",
        "at_plz",
        "fr_code_postal",
        "au_postcode",
    )

    def validate_geo_systems(self, targeting: Targeting) -> list[str]:
        """Validate that targeting geo systems are supported by this adapter.

        Checks both include and exclude fields for geo_metros and geo_postal_areas.
        Returns list of errors naming the unsupported system and supported alternatives.
        """
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
                system = enum_value(metro.system)
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
                system = enum_value(area.system)
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


class AdapterCreateRequest(BaseModel):
    """What the create path hands an adapter: the buy to place, not the buyer's request.

    The mirror of ``AdapterCreateResult`` on the way in. A ``CreateMediaBuyRequest`` is
    the ACCEPTED SHAPE OF A REQUEST — it requires ``idempotency_key`` and ``account``
    because a buyer sending one must supply them. An adapter reads neither, and the
    approval executor is not a request at all: it replays a buy the seller already
    accepted, off the row. Handing the DTO to adapters forced that replay to rebuild one
    and to FABRICATE an idempotency key for rows stored before the field was required —
    a value no buyer ever sent, in a field whose whole meaning is "what the buyer sent".

    So this carries exactly what the adapters read, and nothing a buyer must send:

    * ``brand`` — the campaign name every adapter derives, through ``brand_key_parts``.
    * ``po_number`` — the buy's id prefix and the order/campaign name.
    * ``total_budget`` — one number, already summed. ``CreateMediaBuyRequest`` computes
      it from its packages (``get_total_budget``); the replay reads the row's column.
    * ``already_approved`` — the approval executor's replay flag, a declared field
      instead of the ``setattr(request, "_already_approved", True)`` it replaces.
    * ``push_notification_config`` — GAM's webhook target for background order approval.

    A field an adapter does not read does not belong here; ``extra="forbid"`` says so.

    There are exactly TWO sources a carrier is ever built from — a buyer's validated
    request, and the same request as the row persisted it — so there are exactly two
    constructors, both below, and no caller builds one by hand. That is not a style
    preference: the approval replay built its own and silently omitted
    ``push_notification_config``, so a buy approved off the queue never told the buyer
    its order had been approved. Neither constructor NAMES the buyer-supplied fields;
    both read ``_buyer_supplied_fields()``, which is derived from the declared fields.
    A field added to this class therefore reaches both sources with no further edit,
    which is what keeps the two from disagreeing again.
    """

    model_config = ConfigDict(extra="forbid")

    brand: BrandReference | dict[str, Any] | str | None = None
    po_number: str | None = None
    total_budget: Decimal = Decimal(0)
    already_approved: bool = False
    push_notification_config: PushNotificationConfig | None = None

    #: The two fields the SELLER supplies rather than the buyer: ``total_budget`` is
    #: summed by the caller (from the request's packages, or off the row's column), and
    #: ``already_approved`` is the replay flag. Everything else comes from the buyer
    #: under its own name, which is also the key the persisted dump stores it under.
    _SELLER_SUPPLIED: ClassVar[frozenset[str]] = frozenset({"total_budget", "already_approved"})

    @classmethod
    def _buyer_supplied_fields(cls) -> tuple[str, ...]:
        """The declared fields a buyer supplies, in declaration order."""
        return tuple(name for name in cls.model_fields if name not in cls._SELLER_SUPPLIED)

    @classmethod
    def from_buyer_request(cls, req: CreateMediaBuyRequest) -> AdapterCreateRequest:
        """Project a buyer's validated request onto what the adapters read.

        ``already_approved`` is false by definition here: a request arriving from a
        buyer has not been through the approval queue.
        """
        buyer_supplied = {name: getattr(req, name) for name in cls._buyer_supplied_fields()}
        return cls(total_budget=req.get_total_budget(), **buyer_supplied)

    @classmethod
    def from_persisted_request(
        cls,
        raw_request: Mapping[str, Any],
        *,
        total_budget: Decimal,
    ) -> AdapterCreateRequest:
        """Project a PERSISTED request — ``media_buys.raw_request`` — onto the same shape.

        For the approval executor, which replays a buy the seller already accepted. It
        cannot go through ``from_buyer_request``: that takes a ``CreateMediaBuyRequest``,
        and rebuilding the DTO from a row is exactly what forced the executor to
        fabricate an ``idempotency_key`` no buyer ever sent. So the replay reads the dump
        directly — ``raw_request`` is ``CreateMediaBuyRequest.model_dump(mode="json")``
        as ``MediaBuyRepository.create_from_request`` wrote it, so the keys are the
        buyer's field names and each value validates back through this model's own
        annotations.

        ``total_budget`` is passed rather than read from the dump: the row's own budget
        column is the summed figure, and it is what the seller accepted.
        ``already_approved`` is true by definition — this buy came through the queue.
        """
        buyer_supplied = {name: raw_request.get(name) for name in cls._buyer_supplied_fields()}
        return cls(total_budget=total_budget, already_approved=True, **buyer_supplied)


class AdapterCreateResult(BaseModel):
    """What an adapter's ``create_media_buy`` hands back to the tool.

    Read by ``media_buy_create`` after the row is written; the tool builds the buyer's
    ``CreateMediaBuySuccess`` itself from the persisted row. This is never serialized
    to a buyer, so it carries a seller-internal value (the per-package ad-server
    line-item ids) without a wire model having to strip it. It carries exactly what the
    tool reads: a workflow step an adapter opens is tracked by the workflow tables, not
    handed back here.

    ``extra="forbid"`` is what makes "exactly what the tool reads" a check rather than a
    claim: a kwarg no tool reads raises at construction instead of being dropped. One
    adapter kept passing the update response's effective-date field after the carrier
    split precisely because nothing refused it.
    """

    # A carrier, never on the wire: an unknown keyword is a stale field from a deleted
    # carrier version, so it fails at construction instead of being dropped silently.
    model_config = ConfigDict(extra="forbid")

    media_buy_id: str
    packages: list[ResponsePackage]
    creative_deadline: AwareDatetime | None = None
    #: package_id -> ad-server line-item id, persisted as package_config["platform_line_item_id"].
    platform_line_item_ids: dict[str, str] = Field(default_factory=dict)


class AdapterUpdateResult(BaseModel):
    """What an adapter's ``update_media_buy`` hands back to the tool.

    Read by ``media_buy_update`` after the ad server is changed; the tool builds the
    buyer's ``UpdateMediaBuySuccess`` itself from the re-read row. Never serialized to
    a buyer.

    ``extra="forbid"`` for the same reason as ``AdapterCreateResult``.
    """

    # Same reason as AdapterCreateResult: a stale keyword fails loudly.
    model_config = ConfigDict(extra="forbid")

    media_buy_id: str
    affected_packages: list[AffectedPackage]


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

    # Default delivery measurement provider for products created by this adapter.
    # Per AdCP spec, delivery_measurement is REQUIRED on all products.
    # Subclasses should override with their specific measurement provider.
    default_delivery_measurement: dict[str, str] = {"provider": "publisher"}

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
        creative_engine: CreativeEngineAdapter | None = None,
        tenant_id: str | None = None,
    ):
        if not tenant_id:
            raise AdCPConfigurationError()
        self.config = config
        self.principal = principal
        self.principal_id = principal.principal_id  # For backward compatibility
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

    def _require_config(self, value: _ConfigT | None, *, field: str) -> _ConfigT:
        """Return ``value`` when present; otherwise raise ``AdCPConfigurationError``.

        Centralizes the adapter-``__init__`` "required config value is absent"
        guard so every adapter raises the same exception type with the missing
        ``field`` attached to the error. The class, the code and ``field`` are the
        whole diagnosis; no sentence is authored here.

        Returns the value with ``None`` stripped from its type, so callers can
        rebind (``self.x = self._require_config(self.x, ...)``) to narrow the
        attribute for downstream use.
        """
        if value:
            return value
        raise AdCPConfigurationError(field=field)

    def log(self, message: str):
        """Log a message to the adapter console."""
        self.console.print(message)

    def _build_package_responses(
        self,
        packages: list[MediaPackage],
        *,
        paused: bool = False,
        include_product_id: bool = False,
    ) -> list[ResponsePackage]:
        """Build AdCP-compliant package responses from MediaPackage list.

        Per AdCP spec, CreateMediaBuyResponse.Package requires package_id.
        This builds the list consistently across adapters.

        Args:
            packages: List of MediaPackage objects from the request.
            paused: Whether packages should be marked as paused (e.g. for HITL).
            include_product_id: Whether to include product_id in the response
                (useful for adapters that need product tracking, e.g. Mock).

        Returns:
            List of ResponsePackage objects ready for AdapterCreateResult.
        """
        responses = []
        for package in packages:
            kwargs: dict[str, Any] = {
                "package_id": package.package_id,
                "paused": paused,
            }
            if include_product_id:
                kwargs["product_id"] = package.product_id
            responses.append(ResponsePackage(**kwargs))
        return responses

    def _build_create_success(
        self,
        media_buy_id: str,
        packages: list[MediaPackage],
        *,
        paused: bool = False,
        creative_deadline_days: int | None = 2,
        package_responses: list[ResponsePackage] | None = None,
        include_product_id: bool = False,
        platform_line_item_ids: dict[str, str] | None = None,
    ) -> AdapterCreateResult:
        """Build an AdapterCreateResult with standard fields.

        Constructs the result with media_buy_id, creative_deadline,
        and package responses. If package_responses is not provided, builds them
        from the packages list.

        Takes no request: the result is built from the ad server's own ids, and the
        parameter this used to declare was read by nothing.

        Args:
            media_buy_id: The generated media buy ID.
            packages: List of MediaPackage objects from the request.
            paused: Whether packages should be marked as paused.
            creative_deadline_days: Days from now for creative deadline.
                None means no creative deadline (e.g. GAM sets this explicitly).
            package_responses: Pre-built package responses (overrides packages).
            include_product_id: Whether to include product_id in package responses
                (only used when package_responses is None).
            platform_line_item_ids: package_id -> ad-server line-item id, for adapters
                that create one line item per package.

        Returns:
            AdapterCreateResult.
        """
        if package_responses is None:
            package_responses = self._build_package_responses(
                packages, paused=paused, include_product_id=include_product_id
            )
        creative_deadline = (
            datetime.now(UTC) + timedelta(days=creative_deadline_days) if creative_deadline_days is not None else None
        )
        return AdapterCreateResult(
            media_buy_id=media_buy_id,
            creative_deadline=creative_deadline,
            packages=package_responses,
            platform_line_item_ids=platform_line_item_ids or {},
        )

    @staticmethod
    def get_supported_pricing_models() -> set[str]:
        """Return set of pricing models this adapter supports (AdCP PR #88).

        Default implementation supports only CPM. Override in subclasses.
        Staticmethod: no per-principal state, so it can be resolved from the
        adapter CLASS alone via get_adapter_class_for_tenant() (salesagent-r9rf,
        same INV-4 pattern as get_targeting_capabilities(), salesagent-dn2s) —
        capability data must not require a resolved Principal to read.

        Returns:
            Set of pricing model strings: {"cpm", "cpcv", "cpp", "cpc", "cpv", "flat_rate"}
        """
        return {"cpm"}

    @staticmethod
    def get_targeting_capabilities() -> TargetingCapabilities:
        """Return targeting capabilities this adapter supports.

        Default implementation returns minimal capabilities (geo country only).
        Override in subclasses with actual adapter capabilities.

        A ``@staticmethod`` (not an instance method) because capability
        discovery (get_adcp_capabilities) must read this off the adapter
        CLASS, tenant-only, without ever constructing a Principal-bound
        adapter instance (some adapters require principal-bound config in
        ``__init__`` and would crash for a synthetic Principal — see
        salesagent-dn2s). Keeping this a staticmethod makes
        instance-independence a structural fact instead of an unenforced
        convention: any future override that needs ``self`` (e.g. to read
        adapter config) MUST stop being a plain override of this signature,
        which forces a deliberate decision instead of a silent crash.

        Returns:
            TargetingCapabilities describing what targeting is supported
        """
        return TargetingCapabilities(geo_countries=True)

    def validate_media_buy_request(
        self,
        request: AdapterCreateRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> list[ErrorProblem]:
        """Pre-validate a media buy request without creating anything.

        Called before adapter execution to catch
        adapter-specific constraint violations early. Override in
        subclasses to add adapter-specific validation.

        Default implementation validates pricing model compatibility.
        Subclasses can override to add adapter-specific checks (e.g., impressions limits).

        Returns ``ErrorProblem`` rather than strings (salesagent-rys3u.4). An adapter
        CLASSIFIES a constraint violation; it does not describe one. ``ErrorProblem``
        has no free-text field, so the four authored sentences this used to append --
        the last of them a suggestion, the field the epic made a function of the code --
        are not merely deleted but unwritable.

        Returns:
            One problem per violation. Empty list means validation passed.
        """
        problems: list[ErrorProblem] = []
        supported = self.get_supported_pricing_models()

        if package_pricing_info:
            # Position, not just id: package_field_path builds an INDEXED pointer
            # (packages[0].x), which is the shape the pinned contract grades and the
            # only one that says WHICH package on a multi-package request.
            index_of = {pkg.package_id: i for i, pkg in enumerate(packages)}
            for pkg_id, pricing in package_pricing_info.items():
                pricing_model = pricing.get("pricing_model", "")
                if pricing_model and pricing_model.lower() not in supported:
                    problems.append(
                        ErrorProblem(
                            code=ErrorCode.UNSUPPORTED_FEATURE,
                            subject_type="package",
                            # The old message discarded this, so a buyer with three
                            # packages could not tell which one was refused.
                            subject_id=pkg_id,
                            field=package_field_path("pricing_option_id", index_of.get(pkg_id, 0)),
                            rejected_value=pricing_model,
                            # An ARRAY, which is the pin's canonical accepted_values
                            # (core/error.json details description). It was a joined
                            # string, which a machine has to split on ", ".
                            accepted_values=sorted(m.upper() for m in supported),
                        )
                    )

        return problems

    @abstractmethod
    def create_media_buy(
        self,
        request: AdapterCreateRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> AdapterCreateResult:
        """Creates a new media buy on the ad server from selected packages.

        Args:
            request: The buy to place (never the buyer's DTO — see AdapterCreateRequest)
            packages: Simplified package models for adapter
            start_time: Campaign start time
            end_time: Campaign end time
            package_pricing_info: Optional validated pricing information per package (AdCP PR #88)
                Maps package_id → {pricing_model, rate, currency, is_fixed, bid_price}

        Returns:
            AdapterCreateResult with the ad server's ids. Failures are raised as
            AdCPSalesAgentError subclasses, never returned.
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

    def get_packages_snapshot(
        self, package_refs: list[tuple[str, str, str | None]]
    ) -> dict[str, dict[str, Snapshot | None]]:
        """Get near-real-time delivery snapshots for packages.

        Args:
            package_refs: List of (media_buy_id, package_id, platform_line_item_id) tuples.
                platform_line_item_id may be None if the package was not yet pushed to the platform.

        Returns:
            Nested dict: media_buy_id -> package_id -> Snapshot (or None if unavailable).
            Adapters that do not support snapshots should not override this method.
        """
        raise NotImplementedError("Snapshots not supported by this adapter")

    @abstractmethod
    def update_media_buy(
        self,
        media_buy_id: str,
        action: str,
        package_id: str | None,
        budget: int | None,
        today: datetime,
    ) -> AdapterUpdateResult:
        """Updates a media buy with a specific action.

        Failures are raised as AdCPSalesAgentError subclasses, never returned.
        """
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
