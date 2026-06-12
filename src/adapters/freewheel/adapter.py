"""FreeWheel adapter — implements ``AdServerAdapter`` against the Publisher API.

Entity mapping (Mapping A — see docs/adapters/freewheel/):
- AdCP MediaBuy → FreeWheel Insertion Order (the commercial transaction:
  carries budget, schedule, currency, stage)
- AdCP Package  → FreeWheel Placement (the delivery unit, one per package)
- FW Campaign   → per-buy wrapper above the IO (auto-created; carries
  ``advertiser_id`` and groups the IO + its placements)

FreeWheel's data model is three levels (Campaign > IO > Placement). The IO
is the unit of commerce; the Campaign is a grouping layer above. Reusing a
single Campaign across many IOs (the publisher-ideal pattern) would require
state we don't currently have, so v1 creates one Campaign per AdCP MediaBuy.

Live coverage:
- ✅ create_media_buy — creates Campaign + IO + Placement(s) and returns
  the IO id as ``media_buy_id``.
- ✅ check_media_buy_status — reads the IO (not the Campaign).
- ⏳ update_media_buy — write paths verified against the live API and
  available as ``client.commercial.update_insertion_order`` /
  ``update_placement``. Adapter wiring is blocked on two data-model
  gaps that need state we don't currently keep or scopes we don't have:

  1. Per-package pause/resume needs to look up the FW placement_id from
     the AdCP package_id. The v3 placements endpoint does not honour an
     ``?insertion_order_id=X`` filter (returns the full network list)
     and there's no nested-collection endpoint at v3
     (``/insertion_orders/{id}/placements`` returns 404). The v4 nested
     form exists (``/services/v4/insertion_orders/{id}/placements``) but
     our token gets a 403 IAM deny on it — needs publisher scope grant.

  2. Per-package budget changes aren't directly representable: FW's
     budget lives on the IO, not on the placement. update_package_budget
     would either need a one-IO-per-package mapping (a different Mapping
     than A) or per-package budget tracking we don't have.

- ✅ add_creative_assets + associate_creatives — fully unblocked as of
  2026-05-13. Live-verified create→bind→unbind→delete cycle against Talpa.

    * ✅ ``/services/v4/creative_resources`` (POST/GET/DELETE) — manage
      creative records: name, base_ad_unit, renditions (VAST tag URIs or
      hosted content), advertiser scoping. POST body wrapped under
      ``{"creative": {...}}``; response wrapped under
      ``{"data": {"creative": {...}}}``. Exposed on the client at
      ``client.creatives``.
    * ✅ ``/services/v4/creative_instances`` (POST/DELETE) — the
      creative-to-ad_unit_node binding. FW's docs call the body param
      ``ad_id`` but its description says "The Ad Unit Node ID to link
      Creative" — there's no separate Ad object. POST returns 201 with
      ``placement_id`` auto-populated. The adapter looks up
      ad_unit_node_ids per placement from the inventory cache and
      posts one creative_instance per (node, creative) pair.

  AdCP semantic note: ``sync_creatives`` (buyer registering creatives)
  maps cleanly: ``add_creative_assets`` POSTs creative_resources,
  ``associate_creatives`` POSTs creative_instances binding each
  resource to every ad_unit_node under the target placements.

  Demand-side path (out of scope for publisher integration): a buyer
  with their own DSP seat would POST to
  ``/demand/v1/accounts/{seat_id}/ads`` using a separate Demand API
  bearer Talpa doesn't have.
- ⏳ get_media_buy_delivery — reporting lives on a different API surface
  not yet mapped.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from adcp.types.aliases import Package as ResponsePackage

from src.adapters.base import (
    AdapterCapabilities,
    AdapterSyncResult,
    AdServerAdapter,
    CreativeEngineAdapter,
    DeliveryDataUnavailable,
    PermissionsReport,
    TargetingCapabilities,
)
from src.adapters.constants import REQUIRED_UPDATE_ACTIONS
from src.adapters.freewheel.client import DEFAULT_CLIENT_CREDENTIALS_TOKEN_URL, FreeWheelClient, FreeWheelError
from src.adapters.freewheel.formats import freewheel_creative_formats
from src.adapters.freewheel.schemas import FREEWHEEL_HOSTS, FreeWheelConnectionConfig, FreeWheelProductConfig
from src.adapters.freewheel.targeting import build_targeting, validate_targeting
from src.core.database.database_session import get_db_session
from src.core.database.repositories.freewheel_inventory import FreeWheelInventoryRepository
from src.core.database.repositories.freewheel_placement_stats import FreeWheelPlacementStatsRepository
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AssetStatus,
    CheckMediaBuyStatusResponse,
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    Error,
    MediaPackage,
    Principal,
    ReportingPeriod,
    Snapshot,
    UpdateMediaBuyResponse,
    UpdateMediaBuySuccess,
)

logger = logging.getLogger(__name__)

NIGHTLY_FORECAST_CACHE_TTL = timedelta(hours=20)


class FreeWheelAdapter(AdServerAdapter):
    """Adapter for the FreeWheel Publisher API (Comcast Technology Solutions)."""

    adapter_name = "freewheel"

    # FreeWheel's strength is video — OLV + CTV — with display as a secondary surface.
    default_channels = ["olv", "ctv", "display"]
    default_delivery_measurement = {"provider": "freewheel"}

    connection_config_class = FreeWheelConnectionConfig
    product_config_class = FreeWheelProductConfig
    capabilities = AdapterCapabilities(
        supports_inventory_sync=True,
        supports_reporting_sync=True,  # via FreeWheelReportingSync (scope-gated)
        supports_inventory_profiles=True,
        inventory_entity_label="Placements",
        supports_custom_targeting=True,
        supports_geo_targeting=True,
        supports_dynamic_products=False,
        supported_pricing_models=["cpm", "flat_rate"],
        supports_webhooks=False,
        supports_realtime_reporting=False,
    )

    def __init__(
        self,
        config: dict[str, Any],
        principal: Principal,
        dry_run: bool = False,
        creative_engine: CreativeEngineAdapter | None = None,
        tenant_id: str | None = None,
    ):
        """Resolve the bearer token and target environment host.

        Dry-run defers client construction so the adapter can be configured
        before a bearer token is provisioned by FreeWheel (or the publisher).
        """
        super().__init__(config, principal, dry_run, creative_engine, tenant_id)

        self.advertiser_id = self.principal.get_adapter_id("freewheel") or self.config.get("default_advertiser_id")
        if not self.advertiser_id and not self.dry_run:
            raise ValueError(
                f"Principal {principal.principal_id} does not have a FreeWheel advertiser ID "
                "and no default_advertiser_id is configured"
            )

        self.username = self.config.get("username")
        self.password = self.config.get("password")
        self.api_token = self.config.get("api_token")
        self.client_id = self.config.get("client_id")
        self.client_secret = self.config.get("client_secret")
        self.token_url = self.config.get("token_url") or DEFAULT_CLIENT_CREDENTIALS_TOKEN_URL
        self.environment = self.config.get("environment", "production")
        self.base_url = FREEWHEEL_HOSTS.get(self.environment, FREEWHEEL_HOSTS["production"])

        if self.dry_run:
            self.log("Running in dry-run mode — FreeWheel Publisher API calls will be simulated", dry_run_prefix=False)
            self._client: FreeWheelClient | None = None
        else:
            has_client_credentials = bool(self.client_id) and bool(self.client_secret)
            has_password_grant = bool(self.username) and bool(self.password)
            has_token = bool(self.api_token)
            if not (has_client_credentials or has_password_grant or has_token):
                raise ValueError(
                    "FreeWheel config requires one of: (client_id + client_secret), (username + password), or api_token"
                )
            self._client = FreeWheelClient(
                username=self.username,
                password=self.password,
                api_token=self.api_token,
                client_id=self.client_id,
                client_secret=self.client_secret,
                token_url=self.token_url,
                base_url=self.base_url,
            )

    # ----- capabilities -----

    def get_supported_pricing_models(self) -> set[str]:
        return {"cpm", "flat_rate"}

    def get_creative_formats(self) -> list[dict[str, Any]]:
        """Return the static set of VAST video formats this adapter supports.

        FreeWheel delivers video via VAST tag forwarding — the six declared
        formats cover the common pre/mid/post-roll × 15s/30s combinations.
        See :mod:`._formats` for the canonical list and the rationale for
        declaring statically rather than synthesising from synced data.
        """
        return freewheel_creative_formats(self.tenant_id)

    def get_targeting_capabilities(self) -> TargetingCapabilities:
        return TargetingCapabilities(
            geo_countries=True,
            geo_regions=True,
            nielsen_dma=True,
        )

    def run_inventory_sync(self) -> AdapterSyncResult:
        """Refresh the local FreeWheel inventory cache from the API.

        Wraps :class:`FreeWheelInventorySync` and converts its internal
        :class:`SyncResult` shape to the uniform :class:`AdapterSyncResult`
        the shared scheduler expects. Partial failures are captured in
        ``errors`` rather than raised — the scheduler logs the result
        and tries again on the next tick.
        """
        from datetime import UTC
        from datetime import datetime as _datetime

        from src.adapters.base import AdapterSyncResult
        from src.adapters.freewheel.inventory_sync import FreeWheelInventorySync

        start = _datetime.now(UTC)
        if self.dry_run or self._client is None:
            return AdapterSyncResult(
                sync_kind="inventory",
                started_at=start,
                finished_at=_datetime.now(UTC),
                succeeded=False,
                errors={"adapter": "dry-run mode — no live client to sync with"},
            )

        with get_db_session() as session:
            syncer = FreeWheelInventorySync(client=self._client, session=session, tenant_id=self.tenant_id or "default")
            inner = syncer.run()
            session.commit()

        return AdapterSyncResult(
            sync_kind="inventory",
            started_at=inner.started_at or start,
            finished_at=inner.finished_at or _datetime.now(UTC),
            succeeded=inner.succeeded,
            counts=dict(inner.counts),
            errors=dict(inner.errors),
        )

    def run_reporting_sync(
        self,
        *,
        placement_ids: list[str] | None = None,
        start_date: Any = None,
        end_date: Any = None,
    ) -> AdapterSyncResult:
        """Refresh the local FreeWheel placement-stats cache via the
        Query Reporting API.

        Optional kwargs let callers narrow the report window — the
        scheduler uses defaults (today, all placements); the admin
        "Sync Reporting Now" button can override for a specific date
        range or placement set.

        Today this raises :class:`ReportingScopeNotGranted` on its first
        call against most accounts (Tier 1 scope grant is pending for
        ``/reporting/*``). We catch that and return a soft-failed
        :class:`AdapterSyncResult` so the shared scheduler logs the state
        without exception spam and keeps trying.
        """
        from datetime import UTC
        from datetime import datetime as _datetime

        from src.adapters.base import AdapterSyncResult
        from src.adapters.freewheel._reporting import ReportingError
        from src.adapters.freewheel.reporting_sync import (
            FreeWheelReportingSync,
            ReportingScopeNotGranted,
        )

        start = _datetime.now(UTC)
        if self.dry_run or self._client is None:
            return AdapterSyncResult(
                sync_kind="reporting",
                started_at=start,
                finished_at=_datetime.now(UTC),
                succeeded=False,
                errors={"adapter": "dry-run mode — no live client to sync with"},
            )

        with get_db_session() as session:
            syncer = FreeWheelReportingSync(client=self._client, tenant_id=self.tenant_id or "default", session=session)
            try:
                inner = syncer.run(placement_ids=placement_ids, start_date=start_date, end_date=end_date)
            except ReportingScopeNotGranted as exc:
                return AdapterSyncResult(
                    sync_kind="reporting",
                    started_at=start,
                    finished_at=_datetime.now(UTC),
                    succeeded=False,
                    errors={"scope": str(exc)},
                    metadata={"scope_pending": True},
                )
            except ReportingError as exc:
                return AdapterSyncResult(
                    sync_kind="reporting",
                    started_at=start,
                    finished_at=_datetime.now(UTC),
                    succeeded=False,
                    errors={"reporting_client": str(exc)},
                )

        return AdapterSyncResult(
            sync_kind="reporting",
            started_at=start,
            finished_at=_datetime.now(UTC),
            succeeded=inner.error is None,
            counts={"placements": inner.placements_updated},
            errors={"job": inner.error} if inner.error else {},
            metadata={"job_id": inner.job_id} if inner.job_id else {},
        )

    def latest_inventory_sync_at(self) -> datetime | None:
        """Most-recent ``last_synced_at`` across cached inventory rows."""
        with get_db_session() as session:
            return FreeWheelInventoryRepository(session, self.tenant_id or "default").latest_sync_at()

    def latest_reporting_sync_at(self) -> datetime | None:
        """Most-recent ``last_synced_at`` across cached placement stats."""
        from src.core.database.repositories.freewheel_placement_stats import (
            FreeWheelPlacementStatsRepository,
        )

        with get_db_session() as session:
            return FreeWheelPlacementStatsRepository(session, self.tenant_id or "default").latest_sync_at()

    async def get_available_inventory(self) -> dict[str, Any]:
        """Surface the locally-synced FW taxonomy for AI product configuration.

        Reads from the ``freewheel_inventory`` cache (refreshed via the
        Sync Inventory button or :class:`FreeWheelInventorySync`). No FW
        API calls happen here — everything is served from the local cache
        so the AI product configurator can run offline.

        Shape follows the base ``get_available_inventory`` contract:

        * ``placements`` — FW ``ad_unit_packages`` (the buyer-facing bundles)
        * ``ad_units``   — FW sites + site_sections (where ads can run)
        * ``targeting_options`` — ``standard_attributes`` grouped by parent
          taxonomy key (genres, tv_ratings, languages, device_types, …)
        * ``creative_specs`` — the static VAST format declarations
        * ``properties`` — counts and metadata about the synced cache
        """
        with get_db_session() as session:
            repo = FreeWheelInventoryRepository(session, self.tenant_id or "default")
            sites = repo.list_by_type("site")
            site_sections = repo.list_by_type("site_section")
            video_groups = repo.list_by_type("video_group")
            series = repo.list_by_type("series")
            ad_unit_packages = repo.list_by_type("ad_unit_package")
            standard_attrs = repo.list_by_type("standard_attribute")

            placements = [
                {
                    "id": f"ad_unit_package:{row.entity_id}",
                    "name": row.name or row.entity_id,
                    "type": "ad_unit_package",
                }
                for row in ad_unit_packages
            ]

            ad_units = [
                {"path": f"site:{row.entity_id}", "name": row.name or row.entity_id, "type": "site"} for row in sites
            ] + [
                {
                    "path": f"site_section:{row.entity_id}",
                    "name": row.name or row.entity_id,
                    "type": "site_section",
                    "parent": row.parent_id,
                }
                for row in site_sections
            ]

            targeting_options: dict[str, list[dict[str, Any]]] = {}
            for row in standard_attrs:
                bucket = row.parent_id or "uncategorized"
                targeting_options.setdefault(bucket, []).append(
                    {"id": row.entity_id, "name": row.name or row.entity_id}
                )

            properties = {
                "sites_count": len(sites),
                "site_sections_count": len(site_sections),
                "series_count": len(series),
                "video_groups_count": len(video_groups),
                "ad_unit_packages_count": len(ad_unit_packages),
                "standard_attributes_count": len(standard_attrs),
            }

        return {
            "placements": placements,
            "ad_units": ad_units,
            "targeting_options": targeting_options,
            "creative_specs": freewheel_creative_formats(self.tenant_id),
            "properties": properties,
        }

    def check_permissions(self) -> PermissionsReport:
        """Probe every FW endpoint the adapter depends on, return a report."""
        report = self._new_permissions_report(dry_run_message="Dry-run mode — no live FreeWheel client to probe with.")
        if self.dry_run or self._client is None:
            return report

        # Each tuple: (name, description, method, path, required, feature)
        probes: list[tuple[str, str, str, str, bool, str]] = [
            ("auth_token_info", "Validate bearer token", "GET", "/auth/token/info", True, "auth"),
            (
                "v4_inventory_sites",
                "Read inventory taxonomy (v4 sites)",
                "GET",
                "/services/v4/sites?page=1&per_page=1",
                True,
                "inventory_sync",
            ),
            (
                "v4_inventory_ad_unit_packages",
                "Read inventory ad unit packages",
                "GET",
                "/services/v4/ad_unit_packages?page=1&per_page=1",
                True,
                "inventory_sync",
            ),
            (
                "v3_commercial_campaigns",
                "Read/create campaigns (v3 commercial)",
                "GET",
                "/services/v3/campaigns?per_page=1",
                True,
                "create_media_buy",
            ),
            (
                "v3_commercial_insertion_orders",
                "Read/create insertion orders",
                "GET",
                "/services/v3/insertion_orders?per_page=1",
                True,
                "create_media_buy",
            ),
            (
                "v3_commercial_placements",
                "Read/create placements",
                "GET",
                "/services/v3/placements?per_page=1",
                True,
                "create_media_buy",
            ),
            (
                "v3_ad_unit_nodes_read",
                "Read placement→inventory bindings",
                "GET",
                "/services/v3/ad_unit_nodes?per_page=1",
                True,
                "inventory_sync",
            ),
            (
                "v4_creative_resources",
                "Read/create creative resources",
                "GET",
                "/services/v4/creative_resources?page=1&per_page=1",
                True,
                "sync_creatives",
            ),
            (
                # FW's docs name the field ad_id but the description says
                # "The Ad Unit Node ID to link Creative" — there's no
                # separate Ad object. POSTing creative_instances with
                # ad_id=<ad_unit_node_id from inventory sync> binds the
                # creative to the placement (FW auto-populates placement_id).
                # Verified live: 201 Created.
                "v4_creative_instances",
                "Bind creatives to ad_unit_nodes (creative trafficking)",
                "GET",
                "/services/v4/creative_instances?ad_id=1",
                True,
                "creative_trafficking",
            ),
            (
                # The Reporting API lives at /reporting/* (singular) at the
                # host root, NOT under /services/v*. Probing the schema
                # endpoint is cheap and tells us if scope is granted at all;
                # /reporting/jobs is the actual submit URL when wired.
                "reporting_schema",
                "Query Reporting API (introspect available dimensions)",
                "GET",
                "/reporting/dimensions",
                False,
                "delivery_reporting",
            ),
            (
                "reporting_jobs",
                "Query Reporting API (submit + poll delivery report jobs)",
                "GET",
                "/reporting/jobs",
                False,
                "delivery_reporting",
            ),
            (
                "v4_targeting_profiles",
                "Read saved targeting profiles",
                "GET",
                "/services/v4/targeting_profiles?page=1&per_page=1",
                False,
                "advanced_targeting",
            ),
            (
                "v4_audiences",
                "Read audience definitions",
                "GET",
                "/services/v4/audiences?page=1&per_page=1",
                False,
                "audience_targeting",
            ),
            (
                "v4_webhooks",
                "Push state-change notifications via webhooks",
                "GET",
                "/services/v4/webhooks?page=1&per_page=1",
                False,
                "webhooks",
            ),
        ]

        from src.adapters.freewheel.client import FreeWheelAuthError

        # FW's transport probe is content-type-aware (v3 paths return XML,
        # v4 paths return JSON). The base ``_walk_permission_probes`` helper
        # takes a ``probe_fn(method, path) -> (status, body)`` so we bind
        # the accept-header selection here.
        assert self._client is not None  # early-returned above when None
        client = self._client

        def _probe(method: str, path: str) -> tuple[int, str]:
            accept = "application/xml" if "/services/v3/" in path else "application/json"
            return client._transport.probe(method, path, accept=accept)

        try:
            self._walk_permission_probes(report, probes, _probe, auth_error_types=(FreeWheelAuthError,))
        except Exception as exc:
            logger.warning("FreeWheel permissions probe failed unexpectedly: %s", exc)
            report.error = f"Permissions probe failed: {type(exc).__name__}: {exc}"
        return report

    # ----- helpers -----

    def _product_config_from_package(self, package: MediaPackage) -> dict[str, Any]:
        impl = getattr(package, "implementation_config", None) or {}
        return impl.get("freewheel", impl) if isinstance(impl, dict) else {}

    def _line_item_payload(
        self,
        package: MediaPackage,
        rate: float,
        rate_type: str,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, Any]:
        product_config = self._product_config_from_package(package)
        # Dry-run payload — surfaces what the adapter would send to FW.
        # FreeWheel inventory targeting (sites, video_groups, series, ad_unit_package)
        # ultimately becomes ad_unit_nodes attached to the placement; that write
        # path is blocked on the v4 ``ad_unit_nodes`` scope, so for now we just
        # echo the configured intent.
        # Targeting fields that are list-shaped — echo every configured
        # dimension into the dry-run payload so operators can verify intent.
        list_dimensions = (
            "site_ids",
            "site_section_ids",
            "video_group_ids",
            "series_ids",
            "viewership_profile_ids",
            "audience_item_ids",
            "genre_ids",
            "content_daypart_ids",
            "content_duration_ids",
            "content_territory_ids",
            "language_ids",
            "device_type_ids",
            "os_ids",
            "environment_ids",
            "stream_type_ids",
            "subscription_model_ids",
            "addressability_ids",
            "privacy_signal_ids",
            "tv_rating_ids",
        )
        payload: dict[str, Any] = {
            "name": package.name,
            "advertiser_id": self.advertiser_id,
            "start_date": start_time.date().isoformat(),
            "end_date": end_time.date().isoformat(),
            "impression_goal": package.impressions,
            "rate": rate,
            "rate_type": rate_type,
            "ad_unit_package_id": product_config.get("ad_unit_package_id"),
            "price_model": product_config.get("price_model"),
            "targeting": build_targeting(package.targeting_overlay, product_config, tenant_id=self.tenant_id),
            "external_id": package.package_id,
        }
        for dim in list_dimensions:
            payload[dim] = list(product_config.get(dim, []))
        if product_config.get("priority") is not None:
            payload["priority"] = product_config["priority"]
        return payload

    def _pending_creds_error(self, code: str = "pending_credentials") -> CreateMediaBuyError:
        return CreateMediaBuyError(
            errors=[
                Error(
                    code=code,
                    message=(
                        "FreeWheel live-mode operations require staging or production credentials "
                        "and a sandbox-validated JSON shape. Run in dry-run mode until provisioning "
                        "completes."
                    ),
                    details=None,
                )
            ]
        )

    # ----- create_media_buy -----

    def create_media_buy(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> CreateMediaBuyResponse:
        self._audit_create_media_buy(request, start_time, end_time)

        targeting_error = self._validate_targeting_or_error(packages, validate_targeting, adapter_name="FreeWheel")
        if targeting_error is not None:
            return targeting_error

        buy_name = self._buy_name(request)

        if self.dry_run:
            self.log(f"Would call: POST {self.base_url}/services/v3/campaign")
            self.log(f"  Campaign: name={buy_name}, advertiser_id={self.advertiser_id}")
            self.log(f"Would call: POST {self.base_url}/services/v3/insertion_order")
            self.log(
                f"  InsertionOrder: name={buy_name}, campaign_id=<new>, start={start_time.date()}, end={end_time.date()}"
            )
            for package in packages:
                rate, rate_type = self._resolve_pricing_rate(package, package_pricing_info)
                payload = self._line_item_payload(package, rate, rate_type, start_time, end_time)
                self.log(f"Would call: POST {self.base_url}/services/v3/placement")
                self.log(f"  Placement: {payload}")
            return self._build_create_success(request, f"freewheel_{buy_name}", packages)

        # Live mode — Mapping A: Campaign(wrapper) > IO(buy) > Placement(packages).
        assert self._client is not None
        assert self.advertiser_id is not None  # enforced in __init__ for non-dry-run
        try:
            campaign = self._client.commercial.create_campaign(name=buy_name, advertiser_id=int(self.advertiser_id))
            # external_id carries AdCP lineage into FreeWheel so an operator can
            # trace an FW IO/placement back to the buy/package. Validated against
            # the sandbox (persists on both IO and placement). Budget, currency,
            # schedule, and inventory/targeting are NOT sent here: budget/currency
            # require FW network functions disabled on the sandbox (delivery
            # subsystem; MULTI_CURRENCY_SUPPORT) and inventory binding needs the
            # v4 ad_unit_nodes write scope — see the module docstring + the
            # FreeWheel enablement asks. We wire only what we can validate.
            io = self._client.commercial.create_insertion_order(
                name=buy_name,
                campaign_id=campaign.id,
                external_id=request.po_number,
            )
            platform_line_item_ids: dict[str, str] = {}
            package_responses: list[ResponsePackage] = []
            for package in packages:
                placement = self._client.commercial.create_placement(
                    name=package.name or package.package_id,
                    insertion_order_id=io.id,
                    external_id=package.package_id,
                )
                placement_id = str(placement.id)
                platform_line_item_ids[package.package_id] = placement_id
                package_responses.append(
                    ResponsePackage(
                        package_id=package.package_id,
                        paused=False,
                        platform_line_item_id=placement_id,
                    )
                )
        except FreeWheelError as exc:
            logger.warning("FreeWheel create_media_buy failed: %s body=%s", exc, exc.body)
            return CreateMediaBuyError(
                errors=[
                    Error(
                        code="upstream_error",
                        message=f"FreeWheel rejected the request: {exc}",
                        details=None,
                    )
                ]
            )

        response = self._build_create_success(
            request,
            f"freewheel_{io.id}",
            packages,
            package_responses=package_responses,
        )
        object.__setattr__(response, "_platform_line_item_ids", platform_line_item_ids)
        return response

    def _buy_name(self, request: CreateMediaBuyRequest) -> str:
        """Derive a human-readable buy name from the AdCP request.

        Uses po_number when present (the buyer's reference), otherwise falls
        back to a timestamp so we don't collide if a buyer issues multiple
        buys without po_numbers.
        """
        if request.po_number:
            return f"adcp_{request.po_number}"
        return f"adcp_{int(datetime.now(UTC).timestamp())}"

    # ----- creatives -----

    @staticmethod
    def _asset_vast_url(asset: dict[str, Any]) -> str | None:
        """Return the VAST/tag URL from a canonical adapter asset payload."""
        for key in ("vast_tag", "vast_tag_url", "creative_remote_url", "media_url", "url"):
            value = asset.get(key)
            if value:
                return str(value)
        snippet = asset.get("snippet")
        if snippet and str(asset.get("snippet_type") or "").lower() == "vast_url":
            return str(snippet)
        return None

    @staticmethod
    def _asset_duration_seconds(asset: dict[str, Any]) -> int | None:
        """Return an integer duration in seconds from adapter asset metadata."""
        duration = asset.get("duration_seconds", asset.get("duration"))
        if duration is not None:
            return int(float(duration))
        format_ref = asset.get("format_id")
        if isinstance(format_ref, dict) and format_ref.get("duration_ms") is not None:
            return int(float(format_ref["duration_ms"]) / 1000.0)
        return None

    def _creative_renditions(self, asset: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Build inline FreeWheel renditions for the adapter's canonical VAST formats."""
        vast_url = self._asset_vast_url(asset)
        if not vast_url:
            return None
        rendition: dict[str, Any] = {
            "uri": vast_url,
            "content_type": asset.get("content_type") or "application/xml",
            "vast_rendition": True,
            "https_compatibility": "compatible",
        }
        if asset.get("width") is not None:
            rendition["width"] = int(asset["width"])
        if asset.get("height") is not None:
            rendition["height"] = int(asset["height"])
        return [rendition]

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """POST each asset to ``/services/v4/creative_resources`` and return
        AssetStatus carrying the FW-assigned creative_id.

        Each AdCP asset becomes one FW creative_resource. The returned
        ``creative_id`` is the FW resource id — caller threads it into
        :meth:`associate_creatives` so we can bind it to ad_unit_nodes.

        Dry-run logs the planned POST and echoes the AdCP creative_id back
        so callers downstream still get a stable id to plumb through.
        """
        if self.dry_run:
            for asset in assets:
                renditions = self._creative_renditions(asset)
                self.log(
                    f"Would POST {self.base_url}/services/v4/creative_resources "
                    f"name={asset.get('name')} advertiser_id={self.advertiser_id} "
                    f"renditions={renditions or []}"
                )
                self.log(
                    f"  Then POST creative_instances for ad_unit_nodes under {asset.get('package_assignments', [])}"
                )
            return [AssetStatus(creative_id=a["creative_id"], status="approved") for a in assets]

        assert self._client is not None
        statuses: list[AssetStatus] = []
        for asset in assets:
            creative_id = str(asset.get("creative_id") or "")
            renditions = self._creative_renditions(asset)
            if not creative_id:
                logger.warning("FreeWheel asset missing 'creative_id'; skipping")
                statuses.append(AssetStatus(creative_id="", status="failed"))
                continue
            if not renditions:
                logger.warning("FreeWheel asset %s missing VAST/tag URL; skipping", creative_id)
                statuses.append(AssetStatus(creative_id=creative_id, status="failed"))
                continue
            try:
                create_kwargs: dict[str, Any] = {
                    "name": asset.get("name") or f"adcp-{creative_id}",
                    "advertiser_ids": [int(self.advertiser_id)] if self.advertiser_id else None,
                    "base_ad_unit_id": asset.get("base_ad_unit_id"),
                    "external_id": creative_id,
                    "renditions": renditions,
                }
                duration = self._asset_duration_seconds(asset)
                if duration is not None:
                    create_kwargs["duration"] = duration
                created = self._client.creatives.create_creative(**create_kwargs)
                # Echo the FW id back so associate_creatives can use it
                # as platform_creative_ids[] input.
                statuses.append(AssetStatus(creative_id=str(created.id), status="approved"))
            except FreeWheelError as exc:
                logger.warning(
                    "FreeWheel creative_resource create failed for asset %s: %s",
                    creative_id,
                    exc,
                )
                statuses.append(AssetStatus(creative_id=creative_id, status="failed"))
        return statuses

    def associate_creatives(self, line_item_ids: list[str], platform_creative_ids: list[str]) -> list[dict[str, Any]]:
        """Bind FW creatives to FW placements via creative_instances.

        FW's data model: creative_instance binds a creative to an
        ad_unit_node (the placement→inventory binding row). One placement
        has N ad_unit_nodes (pre-roll, mid-roll, post-roll, etc.) — to
        traffic a creative against a placement we POST one creative_instance
        per ad_unit_node under it.

        ``line_item_ids`` here are FW placement IDs. We look up the
        ad_unit_nodes for each placement from the synced inventory cache
        (``freewheel_inventory`` parent_id=placement_id, entity_type=
        ad_unit_node) and POST creative_instances for each (node, creative)
        pair.

        Returns one row per attempted binding with status="success" /
        "failed" / "skipped" + a message explaining the failure or skip.
        """
        if self.dry_run:
            for li in line_item_ids:
                for ci in platform_creative_ids:
                    self.log(
                        f"Would look up ad_unit_nodes for placement={li}, "
                        f"then POST .../creative_instances ad_id=<each node> creative_id={ci}"
                    )
            return [
                {"line_item_id": li, "creative_id": ci, "status": "success"}
                for li in line_item_ids
                for ci in platform_creative_ids
            ]

        assert self._client is not None
        results: list[dict[str, Any]] = []

        # Build placement_id → [ad_unit_node_ids] lookup from the cache.
        # One query per unique placement (small set in practice).
        node_ids_by_placement: dict[str, list[str]] = {}
        with get_db_session() as session:
            repo = FreeWheelInventoryRepository(session, self.tenant_id or "default")
            for placement_id in set(line_item_ids):
                rows = repo.list_by_type("ad_unit_node", parent_id=placement_id)
                node_ids_by_placement[placement_id] = [row.entity_id for row in rows]

        for placement_id in line_item_ids:
            ad_unit_nodes = node_ids_by_placement.get(placement_id, [])
            if not ad_unit_nodes:
                for ci in platform_creative_ids:
                    results.append(
                        {
                            "line_item_id": placement_id,
                            "creative_id": ci,
                            "status": "skipped",
                            "message": (
                                f"No ad_unit_nodes cached for placement {placement_id} — run inventory sync first."
                            ),
                        }
                    )
                continue

            for ci in platform_creative_ids:
                for node_id in ad_unit_nodes:
                    try:
                        binding = self._client.creatives.create_creative_instance(
                            ad_unit_node_id=int(node_id),
                            creative_id=int(ci),
                        )
                        results.append(
                            {
                                "line_item_id": placement_id,
                                "creative_id": ci,
                                "ad_unit_node_id": node_id,
                                "creative_instance_id": binding.get("id"),
                                "status": "success",
                            }
                        )
                    except FreeWheelError as exc:
                        logger.warning(
                            "FreeWheel creative_instance bind failed for placement=%s node=%s creative=%s: %s",
                            placement_id,
                            node_id,
                            ci,
                            exc,
                        )
                        results.append(
                            {
                                "line_item_id": placement_id,
                                "creative_id": ci,
                                "ad_unit_node_id": node_id,
                                "status": "failed",
                                "message": str(exc),
                            }
                        )
        return results

    # ----- status / delivery -----

    def check_media_buy_status(self, media_buy_id: str, today: datetime) -> CheckMediaBuyStatusResponse:
        io_id = media_buy_id.removeprefix("freewheel_")
        if self.dry_run:
            self.log(f"Would call: GET {self.base_url}/services/v3/insertion_orders/{io_id}")
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="active")
        assert self._client is not None
        try:
            io = self._client.commercial.get_insertion_order(int(io_id))
            # The IO carries its booking state on ``stage`` (NOT_BOOKED, BOOKED, etc.);
            # ``status`` is reserved for placement/campaign-level lifecycle.
            status_value = (io.stage or io.status or "active").lower()
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status=status_value)
        except FreeWheelError as exc:
            logger.warning("FreeWheel get_insertion_order failed: %s", exc)
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="unknown")

    def get_media_buy_delivery(
        self, media_buy_id: str, date_range: ReportingPeriod, today: datetime
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Aggregate delivery totals from the placement-stats cache.

        Live mode reads ``freewheel_placement_stats`` (populated by the
        reporting sync job — pending Tier 2 FW scope). When the cache is
        empty (sync not running yet, or scope still pending), raises
        :class:`DeliveryDataUnavailable` so the impl layer can surface a
        ``data_unavailable`` error rather than a zero-delivery response.

        Returning zeros silently was misleading buyers and the delivery
        webhook scheduler (which would fire false "delivering=0" signals
        every hour). Raising lets the AdCP layer respond with a clear
        "no data yet" error code, which the scheduler skips on.

        Dry-run returns simulated numbers for demo/testing.
        """
        if self.dry_run:
            return self._simulated_delivery_response(
                media_buy_id, date_range, today, target_impressions=750_000, cpm=18.0, completion_rate=0.85
            )

        insertion_order_id = media_buy_id.removeprefix("freewheel_")
        with get_db_session() as session:
            repo = FreeWheelPlacementStatsRepository(session, self.tenant_id or "default")
            stats_rows = repo.list_by_insertion_order(insertion_order_id)

        forecast_backed = False
        if self._nightly_forecast_cache_stale(stats_rows, today):
            refreshed_rows = self._refresh_nightly_forecasts_for_media_buy(media_buy_id)
            if refreshed_rows:
                stats_rows = self._merge_stats_by_placement(stats_rows, refreshed_rows)
                forecast_backed = True
        if not stats_rows:
            raise DeliveryDataUnavailable(media_buy_id)
        response = self._aggregate_stat_rows_to_delivery_response(
            media_buy_id,
            date_range,
            stats_rows,
            package_id_attr="placement_id",
        )
        if forecast_backed:
            response.ext = {
                "data_source": "freewheel_nightly_forecast",
                "partial_data": True,
                "note": "Latest FreeWheel nightly forecast snapshot, not an exact report for the requested period.",
            }
        return response

    def get_packages_snapshot(
        self, package_refs: list[tuple[str, str, str | None]]
    ) -> dict[str, dict[str, Snapshot | None]]:
        """Near-real-time package snapshots from the placement-stats cache.

        Reads ``freewheel_placement_stats`` rows for the FW placement IDs
        in ``package_refs`` and returns one :class:`Snapshot` per package.
        Missing rows (no reporting sync data yet, or no scope granted)
        surface as ``None`` so the caller can render a 'no data' state
        rather than failing.
        """
        from src.core.schemas import DeliveryStatus

        now = datetime.now(UTC)
        result: dict[str, dict[str, Snapshot | None]] = {}
        placement_ids = [ref[2] for ref in package_refs if ref[2] is not None]

        if not placement_ids:
            for media_buy_id, package_id, _ in package_refs:
                result.setdefault(media_buy_id, {})[package_id] = None
            return result

        with get_db_session() as session:
            repo = FreeWheelPlacementStatsRepository(session, self.tenant_id or "default")
            stats = repo.get_by_placement_ids(placement_ids)

        stale_refs = [
            ref
            for ref in package_refs
            if ref[2] is not None and (ref[2] not in stats or self._nightly_forecast_cache_stale([stats[ref[2]]], now))
        ]
        if stale_refs and not self.dry_run and self._client is not None:
            refreshed = self._refresh_nightly_forecasts_for_package_refs(stale_refs)
            if refreshed:
                stats.update(self._stats_by_placement(refreshed))

        for media_buy_id, package_id, placement_id in package_refs:
            row = stats.get(placement_id) if placement_id else None
            if row is None:
                result.setdefault(media_buy_id, {})[package_id] = None
                continue

            as_of = row.as_of if row.as_of.tzinfo else row.as_of.replace(tzinfo=UTC)
            staleness_seconds = max(0, int((now - as_of).total_seconds()))

            delivery_status: DeliveryStatus | None = None
            if row.delivery_status:
                # Trust the FW-reported state when present; map common
                # values to the AdCP enum, leave unknown ones as None.
                lower = row.delivery_status.lower()
                if lower in ("delivering", "active"):
                    delivery_status = DeliveryStatus.delivering
                elif lower in ("completed", "complete"):
                    delivery_status = DeliveryStatus.completed
                elif lower in ("paused", "not_delivering", "inactive"):
                    delivery_status = DeliveryStatus.not_delivering
                elif lower in ("exhausted", "budget_exhausted"):
                    delivery_status = DeliveryStatus.budget_exhausted

            result.setdefault(media_buy_id, {})[package_id] = Snapshot(
                as_of=as_of,
                impressions=float(row.impressions or 0),
                spend=(row.spend_micros or 0) / 1_000_000.0,
                clicks=float(row.clicks) if row.clicks is not None else None,
                staleness_seconds=staleness_seconds,
                delivery_status=delivery_status,
                currency=row.currency,
            )
        return result

    def _refresh_nightly_forecasts_for_media_buy(self, media_buy_id: str) -> list[Any]:
        """Fetch nightly forecast rows for the packages stored on one buy."""
        if self.dry_run or self._client is None:
            return []

        with get_db_session() as session:
            media_buy_repo = MediaBuyRepository(session, self.tenant_id or "default")
            package_refs: list[tuple[str, str, str | None]] = []
            for package in media_buy_repo.get_packages(media_buy_id):
                placement_id = (package.package_config or {}).get("platform_line_item_id")
                if placement_id:
                    package_refs.append((media_buy_id, package.package_id, str(placement_id)))

        return self._refresh_nightly_forecasts_for_package_refs(package_refs)

    def _refresh_nightly_forecasts_for_package_refs(self, package_refs: list[tuple[str, str, str | None]]) -> list[Any]:
        """Fetch and cache nightly forecast snapshots for package refs.

        Returns freshly-read cache rows. Individual placement failures are
        logged and skipped so one missing/permission-denied placement does not
        hide data for the rest of the buy.
        """
        if self.dry_run or self._client is None:
            return []

        rows: list[dict[str, Any]] = []
        for media_buy_id, _package_id, placement_id in package_refs:
            if not placement_id:
                continue
            try:
                forecast = self._client.forecasting.nightly_forecast(placement_id)
            except FreeWheelError as exc:
                logger.info("FreeWheel nightly forecast unavailable for placement %s: %s", placement_id, exc)
                continue
            rows.append(self._forecast_to_stats_row(forecast, media_buy_id.removeprefix("freewheel_")))

        if not rows:
            return []

        with get_db_session() as session:
            repo = FreeWheelPlacementStatsRepository(session, self.tenant_id or "default")
            repo.bulk_upsert(rows)
            session.commit()
            return list(repo.get_by_placement_ids(row["placement_id"] for row in rows).values())

    @staticmethod
    def _stats_by_placement(stats_rows: list[Any]) -> dict[str, Any]:
        return {str(row.placement_id): row for row in stats_rows}

    @staticmethod
    def _merge_stats_by_placement(cached_rows: list[Any], refreshed_rows: list[Any]) -> list[Any]:
        rows_by_placement = FreeWheelAdapter._stats_by_placement(cached_rows)
        rows_by_placement.update(FreeWheelAdapter._stats_by_placement(refreshed_rows))
        return list(rows_by_placement.values())

    @staticmethod
    def _nightly_forecast_cache_stale(stats_rows: list[Any], now: datetime) -> bool:
        """Return True when cached forecast/reporting rows are old enough to refresh.

        The delivery webhook scheduler runs hourly but sends at most once per
        24 hours. A 20-hour TTL refreshes the first daily send after FreeWheel's
        nightly forecast has had a chance to roll forward, without hammering the
        endpoint during dashboard polling.
        """
        if not stats_rows:
            return True

        now_utc = now if now.tzinfo else now.replace(tzinfo=UTC)
        for row in stats_rows:
            synced_at = getattr(row, "last_synced_at", None)
            if not isinstance(synced_at, datetime):
                synced_at = getattr(row, "as_of", None)
            if not isinstance(synced_at, datetime):
                return True
            synced_at_utc = synced_at if synced_at.tzinfo else synced_at.replace(tzinfo=UTC)
            if now_utc - synced_at_utc >= NIGHTLY_FORECAST_CACHE_TTL:
                return True
        return False

    @staticmethod
    def _forecast_to_stats_row(forecast: Any, insertion_order_id: str | None) -> dict[str, Any]:
        run_time = FreeWheelAdapter._parse_forecast_run_time(getattr(forecast, "run_time", None))
        delivered_budget = getattr(forecast, "delivered_budget", None)
        return {
            "placement_id": str(forecast.placement_id),
            "insertion_order_id": insertion_order_id,
            "impressions": int(getattr(forecast, "delivered_impressions", None) or 0),
            "completed_views": None,
            "clicks": None,
            "spend_micros": FreeWheelAdapter._decimal_to_micros(delivered_budget),
            "currency": getattr(forecast, "exchange_currency", None),
            "delivery_status": "delivering" if (getattr(forecast, "delivered_impressions", None) or 0) > 0 else None,
            "as_of": run_time,
            "last_synced_at": datetime.now(UTC),
        }

    @staticmethod
    def _decimal_to_micros(value: Decimal | None) -> int:
        if value is None:
            return 0
        return int(value * Decimal("1000000"))

    @staticmethod
    def _parse_forecast_run_time(value: str | None) -> datetime:
        if not value:
            return datetime.now(UTC)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    # FreeWheel performance index updates aren't yet wired — base default applies.

    # ----- update_media_buy -----

    def update_media_buy(
        self,
        media_buy_id: str,
        action: str,
        package_id: str | None,
        budget: int | None,
        today: datetime,
    ) -> UpdateMediaBuyResponse:
        if action not in REQUIRED_UPDATE_ACTIONS:
            return self._unsupported_action_error(action)

        if self.dry_run:
            campaign_id = media_buy_id.removeprefix("freewheel_")
            if action == "pause_media_buy":
                self.log(f"Would PATCH .../campaigns/{campaign_id} status=paused")
            elif action == "resume_media_buy":
                self.log(f"Would PATCH .../campaigns/{campaign_id} status=active")
            elif action in {"pause_package", "resume_package"} and package_id:
                self.log(f"Would PATCH line item externalId={package_id} status={action}")
            elif action in {"update_package_budget", "update_package_impressions"} and package_id and budget:
                self.log(f"Would PATCH line item externalId={package_id} goal={budget}")
            return UpdateMediaBuySuccess(media_buy_id=media_buy_id, affected_packages=[], implementation_date=today)

        # Live mode — pending credential validation.
        from src.core.schemas import UpdateMediaBuyError

        return UpdateMediaBuyError(
            errors=[
                Error(
                    code="pending_credentials",
                    message="FreeWheel live-mode update_media_buy pending sandbox validation",
                    details=None,
                )
            ]
        )
