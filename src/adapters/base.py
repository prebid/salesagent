from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from src.core.schemas import Snapshot, Targeting

from adcp.types.aliases import Package as ResponsePackage
from pydantic import BaseModel, ConfigDict, Field
from rich.console import Console

from src.adapters.constants import REQUIRED_UPDATE_ACTIONS
from src.core.audit_logger import get_audit_logger
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AssetStatus,
    CheckMediaBuyStatusResponse,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    CreateMediaBuySuccess,
    Error,
    MediaPackage,
    PackagePerformance,
    Principal,
    ReportingPeriod,
    UpdateMediaBuyError,
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

    # Reporting sync — populates the per-adapter delivery cache that
    # feeds get_packages_snapshot / get_media_buy_delivery. Different
    # from supports_realtime_reporting (which is a buyer-facing
    # capability flag); this controls whether the AdapterSyncScheduler
    # should periodically run adapter.run_reporting_sync() for this
    # adapter.
    supports_reporting_sync: bool = False

    # Freshness windows for the cross-tenant /admin/scheduling page
    # (#382 Stage 4). ``warning`` = "should refresh soon", ``critical`` =
    # "data is too old, alert operator". Defaults reflect the typical
    # cadences across our adapters — overrides go on the per-adapter
    # ``capabilities = AdapterCapabilities(...)`` block.
    #
    # Adapters with no inventory/reporting support leave these at the
    # default; the scheduling view skips those rows entirely.
    inventory_freshness_warning: timedelta = timedelta(hours=24)
    inventory_freshness_critical: timedelta = timedelta(hours=72)
    reporting_freshness_warning: timedelta = timedelta(hours=2)
    reporting_freshness_critical: timedelta = timedelta(hours=6)

    # Per-adapter "reporting bundled with inventory" hint. GAM doesn't
    # have a separate reporting sync — line-item stats are written by
    # gam_orders_service as part of the inventory sync. The scheduling
    # page surfaces this label so admins don't see "no reporting" and
    # worry that data is missing.
    reporting_bundled_with_inventory: bool = False

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


@dataclass
class PermissionCheck:
    """Result of probing one permission an adapter needs.

    Used by ``AdServerAdapter.check_permissions()`` so operators (and embedders)
    can see at-connect time whether the upstream credentials have the scopes
    every AdCP feature path depends on — rather than discovering a missing
    permission mid-campaign.
    """

    name: str  # short, machine-stable identifier (e.g. "read_creative_resources")
    description: str  # human-readable, "Read creative resources"
    granted: bool
    required: bool = True  # True = blocks a core flow; False = nice-to-have
    feature: str | None = None  # AdCP feature this enables (e.g. "creative_trafficking")
    probe_target: str | None = None  # endpoint/method probed, for operator debugging
    detail: str | None = None  # human-readable reason when not granted


class DeliveryDataUnavailable(Exception):
    """Adapter signals it has no delivery data for this media buy *yet*.

    Distinct from a hard adapter error: the integration is healthy, we
    just don't have data to report. Surfaces as an AdCP error with code
    ``data_unavailable`` so the delivery-webhook scheduler can skip
    firing a webhook (instead of pushing misleading zero-delivery
    signals) and so buyers polling delivery see a clear "no data yet"
    response rather than fake zeros.

    Typical causes: the reporting sync hasn't run yet, or the upstream
    Reporting API scope is still pending. Both are expected pre-GA
    states that should fail soft, not loud.
    """

    def __init__(self, media_buy_id: str, reason: str | None = None) -> None:
        self.media_buy_id = media_buy_id
        self.reason = reason
        super().__init__(f"Delivery data not yet available for {media_buy_id}" + (f": {reason}" if reason else ""))


@dataclass
class AdapterSyncResult:
    """Uniform outcome of one adapter sync run (inventory or reporting).

    Returned by ``AdServerAdapter.run_inventory_sync()`` and
    ``run_reporting_sync()``. The shared ``AdapterSyncScheduler``
    persists these into the ``sync_jobs`` table for the
    ``/admin/scheduling`` page to render.

    ``counts`` is a free-form per-kind tally (entity_type for inventory,
    placement-style breakdowns for reporting). ``errors`` captures
    partial failures so a sync that succeeded for some entity types
    but failed others isn't reported as a total wash.
    """

    sync_kind: str  # "inventory" | "reporting"
    started_at: datetime
    finished_at: datetime
    succeeded: bool
    counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    # Free-form per-sync metadata: reporting carries job_id +
    # placements_updated; inventory carries cache-refresh timestamps,
    # etc. Surfaced by the admin UI; not used by the scheduler logic.
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_count(self) -> int:
        return sum(self.counts.values())


@dataclass
class PermissionsReport:
    """Adapter's view of which upstream permissions are currently granted.

    Returned by ``AdServerAdapter.check_permissions()``. ``fully_operational``
    rolls up to True only when every ``required`` check passes; surface this in
    admin UIs so operators see one-glance status.
    """

    adapter: str
    tenant_id: str | None
    checked_at: datetime
    fully_operational: bool
    checks: list[PermissionCheck]
    error: str | None = None  # set when the probe itself couldn't run (e.g. bad creds)


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
        dry_run: bool = False,
        creative_engine: CreativeEngineAdapter | None = None,
        tenant_id: str | None = None,
    ):
        if not tenant_id:
            raise ValueError(
                "tenant_id is required for adapter initialization. All tenant-scoped operations need a valid tenant_id."
            )
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

    def _audit_create_media_buy(self, request: CreateMediaBuyRequest, start_time: datetime, end_time: datetime) -> None:
        """Emit the standard create_media_buy audit log entry.

        Adapters use this to record the operation against the principal +
        adapter advertiser ID. Centralised here so adapters share one shape
        instead of redeclaring the kwargs.
        """
        adapter_id = getattr(self, "advertiser_id", None) or "unknown"
        self.audit_logger.log_operation(
            operation="create_media_buy",
            principal_name=self.principal.name,
            principal_id=self.principal.principal_id,
            adapter_id=adapter_id,
            success=True,
            details={"po_number": request.po_number, "flight_dates": f"{start_time.date()} to {end_time.date()}"},
        )

    @staticmethod
    def _unsupported_action_error(action: str) -> UpdateMediaBuyError:
        """Build the standard ``UpdateMediaBuyError`` for an action we don't recognise.

        ``REQUIRED_UPDATE_ACTIONS`` is the canonical list every adapter validates
        against; the error message stays consistent across adapters.
        """
        return UpdateMediaBuyError(
            errors=[
                Error(
                    code="unsupported_action",
                    message=f"Action '{action}' not supported. Supported actions: {REQUIRED_UPDATE_ACTIONS}",
                    details=None,
                )
            ]
        )

    def _empty_delivery_response(
        self, media_buy_id: str, reporting_period: ReportingPeriod, *, currency: str = "USD"
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Return a delivery response with zero totals.

        Useful for live-mode stubs in adapters whose reporting flow isn't yet
        wired — the upstream poll loop sees a valid response shape and the
        adapter signals "no delivery yet" rather than crashing.
        """
        from src.core.schemas import DeliveryTotals

        return AdapterGetMediaBuyDeliveryResponse(
            media_buy_id=media_buy_id,
            reporting_period=reporting_period,
            totals=DeliveryTotals(
                impressions=0,
                spend=0.0,
                clicks=0,
                ctr=0.0,
                completed_views=0,
                completion_rate=0.0,
            ),
            by_package=[],
            currency=currency,
        )

    def _simulated_delivery_response(
        self,
        media_buy_id: str,
        reporting_period: ReportingPeriod,
        today: datetime,
        *,
        target_impressions: int,
        cpm: float,
        completion_rate: float = 0.0,
        flight_days: int = 14,
        currency: str = "USD",
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Build a synthetic delivery response for dry-run mode.

        Computes elapsed-flight progress from ``today`` vs ``reporting_period``,
        scales impressions by 95% delivery, and derives spend from CPM. Used by
        adapters whose live-mode reporting flow isn't yet wired — gives buyers
        useful numbers in dry-run without committing to a real reporting call.
        """
        from src.core.schemas import DeliveryTotals

        days_elapsed = max(0, (today.date() - reporting_period.start.date()).days)
        progress = min(days_elapsed / float(flight_days), 1.0)
        impressions = int(target_impressions * progress * 0.95)
        spend = impressions * cpm / 1000
        return AdapterGetMediaBuyDeliveryResponse(
            media_buy_id=media_buy_id,
            reporting_period=reporting_period,
            totals=DeliveryTotals(
                impressions=impressions,
                spend=spend,
                clicks=0,
                ctr=0.0,
                completed_views=int(impressions * completion_rate) if completion_rate else 0,
                completion_rate=completion_rate,
            ),
            by_package=[],
            currency=currency,
        )

    @staticmethod
    def _aggregate_stat_rows_to_delivery_response(
        media_buy_id: str,
        reporting_period: ReportingPeriod,
        stat_rows: list[Any],
        *,
        package_id_attr: str,
        default_currency: str = "USD",
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Aggregate platform stat rows into an AdCP delivery response.

        Shared between reporting-cache adapters (FreeWheel, SpringServe).
        Each row is expected to expose ``impressions``, ``spend_micros``,
        ``completed_views``, and ``currency`` attributes; the per-row
        identifier comes from ``getattr(row, package_id_attr)``.
        """
        from src.core.schemas import AdapterPackageDelivery, DeliveryTotals

        total_impressions = sum(getattr(row, "impressions", 0) or 0 for row in stat_rows)
        total_spend = sum(getattr(row, "spend_micros", 0) or 0 for row in stat_rows) / 1_000_000.0
        total_completed = sum(getattr(row, "completed_views", 0) or 0 for row in stat_rows)
        currency = next((row.currency for row in stat_rows if row.currency), default_currency)
        totals = DeliveryTotals(
            impressions=float(total_impressions),
            spend=total_spend,
            completed_views=float(total_completed) if total_completed else None,
            completion_rate=(total_completed / total_impressions) if total_impressions else None,
        )
        by_package = [
            AdapterPackageDelivery(
                package_id=getattr(row, package_id_attr),
                impressions=int(getattr(row, "impressions", 0) or 0),
                spend=(getattr(row, "spend_micros", 0) or 0) / 1_000_000.0,
                completed_views=int(row.completed_views) if row.completed_views is not None else None,
            )
            for row in stat_rows
        ]
        return AdapterGetMediaBuyDeliveryResponse(
            media_buy_id=media_buy_id,
            reporting_period=reporting_period,
            totals=totals,
            by_package=by_package,
            currency=currency,
        )

    @staticmethod
    def _platform_status_to_delivery_status(status: str | None) -> Any:
        """Translate a platform-reported delivery-status string to the AdCP enum.

        Shared across reporting-cache adapters (FreeWheel, SpringServe).
        Returns ``None`` for unknown/missing values so callers can leave
        the field unset rather than guess. Returns ``DeliveryStatus`` enum.
        """
        from src.core.schemas import DeliveryStatus

        if not status:
            return None
        lower = status.lower()
        if lower in ("delivering", "active"):
            return DeliveryStatus.delivering
        if lower in ("completed", "complete"):
            return DeliveryStatus.completed
        if lower in ("paused", "not_delivering", "inactive"):
            return DeliveryStatus.not_delivering
        if lower in ("exhausted", "budget_exhausted"):
            return DeliveryStatus.budget_exhausted
        return None

    def _wrap_sync_run(
        self,
        sync_kind: str,
        run_callable: Callable[[], Any],
        *,
        scope_error_types: tuple[type[Exception], ...] = (),
        retryable_error_types: tuple[type[Exception], ...] = (),
        rows_count_key: str = "rows",
        rows_attr: str = "rows_updated",
        report_id_attr: str = "report_id",
        error_attr: str = "error",
    ) -> AdapterSyncResult:
        """Run a reporting/inventory sync callable and wrap result into AdapterSyncResult.

        Translates the three outcomes into the uniform shape the shared
        scheduler expects:

        * scope-error (e.g. ``ReportingScopeNotGranted``) -> soft-failed
          result with ``metadata={"scope_pending": True}`` so the scheduler
          keeps retrying without log spam.
        * retryable-error (e.g. ``ReportingError``) -> soft-failed result
          with the error string preserved.
        * success -> ``succeeded`` is True iff the inner result's
          ``error`` attribute is None; counts and report_id surfaced.
        """
        start = datetime.now(UTC)
        try:
            inner = run_callable()
        except scope_error_types as exc:
            return AdapterSyncResult(
                sync_kind=sync_kind,
                started_at=start,
                finished_at=datetime.now(UTC),
                succeeded=False,
                errors={"scope": str(exc)},
                metadata={"scope_pending": True},
            )
        except retryable_error_types as exc:
            return AdapterSyncResult(
                sync_kind=sync_kind,
                started_at=start,
                finished_at=datetime.now(UTC),
                succeeded=False,
                errors={"reporting_client": str(exc)},
            )
        inner_error = getattr(inner, error_attr, None)
        rows_updated = getattr(inner, rows_attr, 0)
        report_id = getattr(inner, report_id_attr, None)
        return AdapterSyncResult(
            sync_kind=sync_kind,
            started_at=start,
            finished_at=datetime.now(UTC),
            succeeded=inner_error is None,
            counts={rows_count_key: rows_updated},
            errors={"job": inner_error} if inner_error else {},
            metadata={"report_id": report_id} if report_id else {},
        )

    @staticmethod
    def _resolve_pricing_rate(
        package: MediaPackage,
        package_pricing_info: dict[str, dict] | None,
        *,
        default_rate_type: str = "CPM",
    ) -> tuple[float, str]:
        """Return ``(rate, rate_type)`` for a package from validated pricing info.

        Adapters use this when translating a ``MediaPackage`` into their ad
        server's flight/line-item payload. ``package_pricing_info`` (AdCP PR #88)
        carries ``{rate, is_fixed, bid_price, pricing_model}`` per package and
        is populated by ``media_buy_create`` before adapter dispatch.
        """
        if not package_pricing_info or package.package_id not in package_pricing_info:
            raise ValueError(
                f"Missing pricing info for package {package.package_id!r}; "
                "media_buy_create must populate package_pricing_info before adapter dispatch."
            )
        pricing = package_pricing_info[package.package_id]
        if pricing.get("is_fixed"):
            rate = pricing["rate"]
        else:
            rate = pricing.get("bid_price")
            if rate is None:
                raise ValueError(f"Package {package.package_id!r} uses auction pricing but has no bid_price.")
        rate_type = "FLAT_RATE" if str(pricing.get("pricing_model", "")).lower() == "flat_rate" else default_rate_type
        return float(rate), rate_type

    @staticmethod
    def _validate_targeting_or_error(
        packages: list[MediaPackage],
        validator: Callable[[Any], list[str]],
        *,
        adapter_name: str,
    ) -> CreateMediaBuyResponse | None:
        """Apply a per-package targeting validator and return an error, or None.

        Iterates each package, collects messages from ``validator``, and packages
        the union into a single ``CreateMediaBuyError`` with the canonical
        ``unsupported_targeting`` code. Returning ``None`` means validation
        passed and the adapter should proceed.
        """
        from src.core.schemas import CreateMediaBuyError

        unsupported_features: list[str] = []
        for package in packages:
            if package.targeting_overlay:
                unsupported_features.extend(validator(package.targeting_overlay))
        if not unsupported_features:
            return None
        return CreateMediaBuyError(
            errors=[
                Error(
                    code="unsupported_targeting",
                    message=f"Unsupported targeting for {adapter_name}: {'; '.join(unsupported_features)}",
                    details=None,
                )
            ]
        )

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
            List of ResponsePackage objects ready for CreateMediaBuySuccess.
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
        request: CreateMediaBuyRequest,
        media_buy_id: str,
        packages: list[MediaPackage],
        *,
        paused: bool = False,
        creative_deadline_days: int | None = 2,
        workflow_step_id: str | None = None,
        package_responses: list[ResponsePackage] | None = None,
        include_product_id: bool = False,
    ) -> CreateMediaBuySuccess:
        """Build a CreateMediaBuySuccess response with standard fields.

        Constructs the response with media_buy_id, creative_deadline,
        and package responses. If package_responses is not provided, builds them
        from the packages list.

        Args:
            request: The original create media buy request.
            media_buy_id: The generated media buy ID.
            packages: List of MediaPackage objects from the request.
            paused: Whether packages should be marked as paused.
            creative_deadline_days: Days from now for creative deadline.
                None means no creative deadline (e.g. GAM sets this explicitly).
            workflow_step_id: Optional workflow step ID for HITL tracking.
            package_responses: Pre-built package responses (overrides packages).
            include_product_id: Whether to include product_id in package responses
                (only used when package_responses is None).

        Returns:
            CreateMediaBuySuccess response.
        """
        if package_responses is None:
            package_responses = self._build_package_responses(
                packages, paused=paused, include_product_id=include_product_id
            )
        creative_deadline = (
            datetime.now(UTC) + timedelta(days=creative_deadline_days) if creative_deadline_days is not None else None
        )
        return CreateMediaBuySuccess(
            media_buy_id=media_buy_id,
            creative_deadline=creative_deadline,
            packages=package_responses,
            status="completed",
            workflow_step_id=workflow_step_id,
        )

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

    def validate_media_buy_request(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> list[str]:
        """Pre-validate a media buy request without creating anything.

        Called before adapter execution (including dry_run) to catch
        adapter-specific constraint violations early. Override in
        subclasses to add adapter-specific validation.

        Default implementation validates pricing model compatibility.
        Subclasses can override to add adapter-specific checks (e.g., impressions limits).

        Returns:
            List of error messages. Empty list means validation passed.
        """
        errors: list[str] = []
        supported = self.get_supported_pricing_models()

        if package_pricing_info:
            for _pkg_id, pricing in package_pricing_info.items():
                pricing_model = pricing.get("pricing_model", "")
                if pricing_model and pricing_model.lower() not in supported:
                    sorted_supported = ", ".join(sorted(s.upper() for s in supported))
                    errors.append(
                        f"Adapter does not support '{pricing_model}' pricing. "
                        f"Supported pricing models: {sorted_supported}. "
                        f"The requested pricing model ('{pricing_model}') is not available. "
                        f"Please choose a product with compatible pricing."
                    )

        return errors

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

    def update_media_buy_performance_index(
        self, media_buy_id: str, package_performance: list[PackagePerformance]
    ) -> bool:
        """Update performance indexes for packages in a media buy.

        Default implementation logs in dry-run and returns success — appropriate
        for ad servers that have no native performance-index endpoint. Adapters
        whose platforms do support priority/index updates (e.g. Kevel) override
        this with a real implementation.
        """
        if self.dry_run:
            for perf in package_performance:
                self.log(f"  {perf.package_id}: index={perf.performance_index:.2f}")
        return True

    @abstractmethod
    def update_media_buy(
        self,
        media_buy_id: str,
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

    def run_inventory_sync(self) -> AdapterSyncResult:
        """Pull this adapter's inventory taxonomy into a local cache.

        Adapters that declare ``capabilities.supports_inventory_sync=True``
        MUST override this method. The shared ``AdapterSyncScheduler``
        calls it on a configurable schedule (default daily); operators
        can also trigger an immediate run via the ``/admin/scheduling``
        page or the per-adapter "Sync Inventory Now" shortcut.

        Implementations should return a non-raising :class:`AdapterSyncResult`
        — partial failures captured in ``errors`` rather than thrown.
        The shared scheduler persists the result to the ``sync_jobs``
        table for the freshness UI to read.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} declared supports_inventory_sync but did not "
            "implement run_inventory_sync(). Override it, or flip the capability flag off."
        )

    def run_reporting_sync(self) -> AdapterSyncResult:
        """Pull delivery metrics into this adapter's stats cache.

        Adapters that declare ``capabilities.supports_reporting_sync=True``
        MUST override this method. Populates whatever cache backs
        :meth:`get_packages_snapshot` and :meth:`get_media_buy_delivery`.

        The shared scheduler runs this hourly (configurable). When the
        upstream API isn't yet authorised (e.g. scope grant pending),
        return a failed-but-non-raising :class:`AdapterSyncResult` so the
        scheduler keeps trying tomorrow without flooding the logs with
        stack traces.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} declared supports_reporting_sync but did not "
            "implement run_reporting_sync(). Override it, or flip the capability flag off."
        )

    def latest_inventory_sync_at(self) -> datetime | None:
        """When this adapter's inventory was last refreshed, or ``None`` if
        never. Used by the ``/admin/scheduling`` freshness display and the
        stale-cache banner. Adapters with inventory caches override this
        to surface their own ``last_synced_at`` column."""
        return None

    def latest_reporting_sync_at(self) -> datetime | None:
        """When this adapter's reporting cache was last refreshed.
        Same shape + same role as :meth:`latest_inventory_sync_at`."""
        return None

    def check_permissions(self) -> PermissionsReport:
        """Probe the upstream API for the scopes this adapter needs.

        Returns a :class:`PermissionsReport` listing each permission this
        adapter depends on along with whether the configured credentials
        currently have it. Operators see this in the admin UI; embedders
        can read it via the tenant-management API.

        Default implementation reports zero checks — adapters with real
        upstream APIs should override and probe their actual endpoints.
        Probes should be cheap (single GET with a 1-row page filter is
        ideal) and should NOT mutate upstream state.
        """
        return PermissionsReport(
            adapter=getattr(self.__class__, "adapter_name", self.__class__.__name__),
            tenant_id=self.tenant_id,
            checked_at=datetime.now(UTC),
            fully_operational=True,  # no checks declared → no failures
            checks=[],
        )

    def _new_permissions_report(self, *, dry_run_message: str | None = None) -> PermissionsReport:
        """Build an empty :class:`PermissionsReport` scaffold for this adapter.

        ``check_permissions`` subclass implementations call this to get a
        consistently-populated report shell; when ``dry_run_message`` is
        supplied and dry-run is active, the report is returned pre-set with
        ``error`` and ``fully_operational=False`` so the caller can return
        it immediately without further setup.
        """
        report = PermissionsReport(
            adapter=getattr(self.__class__, "adapter_name", self.__class__.__name__),
            tenant_id=self.tenant_id,
            checked_at=datetime.now(UTC),
            fully_operational=False,
            checks=[],
        )
        if dry_run_message is not None and self.dry_run:
            report.error = dry_run_message
        return report

    def _walk_permission_probes(
        self,
        report: PermissionsReport,
        probes: list[tuple[str, str, str, str, bool, str]],
        probe_fn: Callable[[str, str], tuple[int, str]],
        *,
        auth_error_types: tuple[type[Exception], ...] = (),
    ) -> None:
        """Run a permission probe matrix into ``report.checks``.

        Each entry in ``probes`` is ``(name, description, method, path,
        required, feature)``. ``probe_fn(method, path)`` must return
        ``(status_code, body_snippet)`` without raising on non-2xx; if it
        does raise an instance of ``auth_error_types`` (e.g. the adapter's
        auth-error class) the walk stops early and ``report.error`` is
        populated. The final ``fully_operational`` rollup is set on the
        report after all probes complete.

        4xx validation errors (400/404/422) count as granted because they
        prove the endpoint accepts the call -- granted is determined by
        status NOT in (401, 403).
        """
        for name, description, method, path, required, feature in probes:
            try:
                status, body = probe_fn(method, path)
            except auth_error_types as exc:
                report.error = f"Authentication failed: {exc}"
                return

            granted = status not in (401, 403)
            detail: str | None = None
            if not granted:
                snippet = body.strip().replace("\n", " ")[:120]
                detail = f"{status}: {snippet}" if snippet else f"HTTP {status}"

            report.checks.append(
                PermissionCheck(
                    name=name,
                    description=description,
                    granted=granted,
                    required=required,
                    feature=feature,
                    probe_target=f"{method} {path.split('?', 1)[0]}",
                    detail=detail,
                )
            )
        report.fully_operational = all(c.granted for c in report.checks if c.required)

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
