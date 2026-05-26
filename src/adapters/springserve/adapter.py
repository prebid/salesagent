"""SpringServe adapter -- implements ``AdServerAdapter`` against the
SpringServe (Magnite) ad-server REST API.

Entity mapping (Mapping A -- see docs/adapters/springserve/):

- AdCP MediaBuy -> SpringServe Campaign (the commercial container: carries
  rate, budget, schedule)
- AdCP Package  -> SpringServe Demand Tag (the active delivery unit: pacing,
  targeting, creative binding)
- AdCP Creative -> SpringServe Video/Audio Creative (POST /api/v0/videos)
  OR VAST URL stored directly on the demand tag

SpringServe has no "Insertion Order" layer above Campaign -- the Campaign
IS the buy. We do not synthesise an IO equivalent.

Stage 1 scope (this commit): skeleton + auth + dry-run for every required
method. Live writes land in Stage 2.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

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
from src.adapters.springserve.client import (
    SpringServeAuthError,
    SpringServeClient,
    SpringServeError,
    SpringServeValidationError,
)
from src.adapters.springserve.formats import springserve_creative_formats
from src.adapters.springserve.schemas import (
    SPRINGSERVE_HOSTS,
    SpringServeConnectionConfig,
    SpringServeProductConfig,
)
from src.adapters.springserve.targeting import (
    build_demand_tag_kv_entries,
    build_demand_tag_targeting,
    validate_targeting,
)
from src.core.database.database_session import get_db_session
from src.core.database.repositories.springserve_demand_tag_stats import (
    SpringServeDemandTagStatsRepository,
)
from src.core.database.repositories.springserve_inventory import (
    SpringServeInventoryRepository,
)
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
    UpdateMediaBuyError,
    UpdateMediaBuyResponse,
    UpdateMediaBuySuccess,
)

logger = logging.getLogger(__name__)


class SpringServeAdapter(AdServerAdapter):
    """Adapter for the SpringServe (Magnite) ad-server REST API."""

    adapter_name = "springserve"

    # Video + CTV is SpringServe's home; audio (Magnite x iHeartMedia
    # marketplace) is first-class on the same API surface.
    default_channels = ["olv", "ctv", "streaming_audio", "podcast"]
    default_delivery_measurement = {"provider": "springserve"}

    connection_config_class = SpringServeConnectionConfig
    product_config_class = SpringServeProductConfig
    capabilities = AdapterCapabilities(
        inventory_entity_label="Supply Tags",
        supports_inventory_sync=True,
        supports_inventory_profiles=True,
        supports_reporting_sync=True,
        supports_geo_targeting=True,
        supports_custom_targeting=True,
        supported_pricing_models=["cpm", "flat_rate"],
        supports_dynamic_products=False,
        supports_realtime_reporting=False,
        supports_webhooks=False,
    )

    def __init__(
        self,
        config: dict[str, Any],
        principal: Principal,
        dry_run: bool = False,
        creative_engine: CreativeEngineAdapter | None = None,
        tenant_id: str | None = None,
    ):
        """Resolve SpringServe demand-partner mapping and authentication.

        SpringServe identifies the demand side by an integer Demand Partner ID
        (the parent of every Campaign + Demand Tag this adapter creates).
        Dry-run skips client construction so an admin can scaffold the adapter
        before token provisioning is complete.
        """
        super().__init__(config, principal, dry_run, creative_engine, tenant_id)

        # SpringServe identifies the demand side by a Demand Partner ID.
        self.demand_partner_id = self.principal.get_adapter_id("springserve") or self.config.get(
            "default_demand_partner_id"
        )
        if not self.demand_partner_id and not self.dry_run:
            raise ValueError(
                f"Principal {principal.principal_id} does not have a SpringServe demand_partner_id "
                "and no default_demand_partner_id is configured"
            )
        # Cast to int -- ORM/JSON may give us a string but the SpringServe
        # API expects integers for IDs.
        if self.demand_partner_id is not None:
            self.demand_partner_id = int(self.demand_partner_id)

        # Secrets are stored encrypted in adapter_config.config_json. The
        # admin UI's test-connection path rehydrates through the schema's
        # field validators (which auto-decrypt); orchestrated sync paths
        # pass the raw JSON, so decrypt defensively here for both.
        from src.adapters._secret_fields import decrypt_secret_value

        self.email = self.config.get("email")
        self.password = decrypt_secret_value(self.config.get("password"))
        self.api_token = decrypt_secret_value(self.config.get("api_token"))
        self.environment = self.config.get("environment", "production")
        self.base_url = SPRINGSERVE_HOSTS.get(self.environment, SPRINGSERVE_HOSTS["production"])
        # Per-tenant provisioning model: 'line_item' = SpringServe hosts the
        # creative (line_item_ratios bind on demand tag); 'tag' = passthrough to
        # a third-party VAST/audio URL (no creative bind). KV targeting is
        # opt-in -- most publishers route audience/context through supply
        # tag selection, not demand_tag_keys. Both are tenant-level adapter
        # config; see SpringServeConnectionConfig for the full rationale.
        # Validate eagerly -- stored ``config_json`` can bypass the Pydantic
        # schema enum if it was written by a path that doesn't round-trip
        # through model_validate (raw DB writes, legacy admin code, etc).
        from src.adapters.springserve._demand_tags import DEMAND_CLASS_WIRE_VALUES

        self.demand_class = self.config.get("demand_class", "line_item")
        if self.demand_class not in DEMAND_CLASS_WIRE_VALUES:
            raise ValueError(
                f"SpringServe demand_class={self.demand_class!r} is not one of "
                f"{sorted(DEMAND_CLASS_WIRE_VALUES.keys())}"
            )
        self.enable_key_value_targeting = bool(self.config.get("enable_key_value_targeting", False))

        if self.dry_run:
            self.log(
                "Running in dry-run mode -- SpringServe API calls will be simulated",
                dry_run_prefix=False,
            )
            self._client: SpringServeClient | None = None
        else:
            has_password_grant = bool(self.email) and bool(self.password)
            has_token = bool(self.api_token)
            if not has_password_grant and not has_token:
                raise ValueError("SpringServe config requires either (email + password) or api_token")
            self._client = SpringServeClient(
                email=self.email,
                password=self.password,
                api_token=self.api_token,
                base_url=self.base_url,
            )

    # ----- capabilities -----

    def get_supported_pricing_models(self) -> set[str]:
        return {"cpm", "flat_rate"}

    def get_creative_formats(self) -> list[dict[str, Any]]:
        """Return the static set of VAST video + audio formats this adapter supports."""
        return springserve_creative_formats(self.tenant_id)

    def get_targeting_capabilities(self) -> TargetingCapabilities:
        return TargetingCapabilities(
            geo_countries=True,
            geo_regions=True,
            nielsen_dma=True,
        )

    def check_permissions(self) -> PermissionsReport:
        """Probe every SpringServe endpoint the adapter depends on."""
        report = self._new_permissions_report(
            dry_run_message="Dry-run mode -- no live SpringServe client to probe with."
        )
        if self.dry_run or self._client is None:
            return report

        probes: list[tuple[str, str, str, str, bool, str]] = [
            ("campaigns_read", "Read/create campaigns", "GET", "/campaigns?per_page=1", True, "create_media_buy"),
            (
                "demand_tags_read",
                "Read/create demand tags (the per-package delivery unit)",
                "GET",
                "/demand_tags?per_page=1",
                True,
                "create_media_buy",
            ),
            (
                "videos_read",
                "Read/upload hosted video creatives",
                "GET",
                "/videos?per_page=1",
                True,
                "sync_creatives",
            ),
            (
                "supply_tags_read",
                "Read supply tags (publisher inventory)",
                "GET",
                "/supply_tags?per_page=1",
                True,
                "inventory_sync",
            ),
            (
                "supply_partners_read",
                "Read supply partners",
                "GET",
                "/supply_partners?per_page=1",
                False,
                "inventory_sync",
            ),
            (
                # Reporting API is POST-only; GET returns 404 with our test
                # account, which our probe correctly treats as "endpoint
                # reachable, scope not denied". Stage 4 replaces this entry
                # with a tiny real POST so we can distinguish "scope granted"
                # from "scope denied" via the 401/403 split.
                "report_submit",
                "Submit delivery report jobs (Reporting API)",
                "GET",
                "/report?per_page=1",
                False,
                "delivery_reporting",
            ),
        ]
        try:
            self._walk_permission_probes(report, probes, self._client.probe, auth_error_types=(SpringServeAuthError,))
        except Exception as exc:
            logger.warning("SpringServe permissions probe failed unexpectedly: %s", exc)
            report.error = f"Permissions probe failed: {type(exc).__name__}: {exc}"
        return report

    # ----- helpers -----

    def _product_config_from_package(self, package: MediaPackage) -> dict[str, Any]:
        impl = getattr(package, "implementation_config", None) or {}
        return impl.get("springserve", impl) if isinstance(impl, dict) else {}

    def _demand_tag_format(self, package: MediaPackage) -> str:
        """Pick the SpringServe ``format`` field (``video`` / ``audio`` /
        ``display``) from the AdCP Package's ``format_ids``.

        SpringServe encodes media type as a string on the demand tag, not
        as a separate field. We discriminate from canonical AdCP format IDs:
        audio formats route to audio, all other supported formats route to
        video.
        """
        for fid in package.format_ids or []:
            fmt_id = fid.id if hasattr(fid, "id") else str(fid)
            if "audio" in fmt_id.lower():
                return "audio"
        return "video"

    def _demand_tag_kwargs(
        self,
        package: MediaPackage,
        campaign_id: int,
        rate: float,
        start_time: datetime,
        end_time: datetime,
        po_number: str | None,
    ) -> dict[str, Any]:
        """Build the kwargs for ``client.demand_tags.create()`` from a package.

        Includes targeting via ``build_demand_tag_targeting`` and the
        per-package demand_code so SpringServe stores the AdCP package_id
        as a searchable code on the tag.
        """
        product_config = self._product_config_from_package(package)
        assert self.demand_partner_id is not None  # enforced in __init__ for live mode
        kwargs: dict[str, Any] = {
            "name": package.name or package.package_id,
            "campaign_id": campaign_id,
            "demand_partner_id": int(self.demand_partner_id),
            "start_date": start_time,
            "end_date": end_time,
            "format": self._demand_tag_format(package),
            "rate": rate,
            "rate_currency": self.config.get("rate_currency", "USD"),
            "demand_code": f"{po_number}_{package.package_id}" if po_number else package.package_id,
            "secondary_code": package.package_id,
            "note": (
                f"Package: {package.name or package.package_id}, Impressions: {package.impressions or 0:,}, CPM: {rate}"
            ),
            "is_active": False,  # Inactive until a creative is bound.
            "demand_class": self.demand_class,
        }
        kwargs.update(
            build_demand_tag_targeting(
                package.targeting_overlay,
                product_config,
                tenant_id=self.tenant_id,
            )
        )
        return kwargs

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

        targeting_error = self._validate_targeting_or_error(packages, validate_targeting, adapter_name="SpringServe")
        if targeting_error is not None:
            return targeting_error

        buy_name = self._buy_name(request)
        rate_currency = self.config.get("rate_currency", "USD")

        if self.dry_run:
            self.log(f"Would call: POST {self.base_url}/campaigns")
            self.log(f"  Campaign: name={buy_name} demand_partner_id={self.demand_partner_id}")
            for package in packages:
                rate, _ = self._resolve_pricing_rate(package, package_pricing_info)
                kwargs = self._demand_tag_kwargs(
                    package,
                    campaign_id=0,  # placeholder for dry-run
                    rate=rate,
                    start_time=start_time,
                    end_time=end_time,
                    po_number=request.po_number,
                )
                self.log(f"Would call: POST {self.base_url}/demand_tags")
                self.log(f"  DemandTag: {kwargs}")
            return self._build_create_success(request, f"springserve_{buy_name}", packages)

        # Live mode -- Mapping A: AdCP MediaBuy -> SS Campaign, AdCP Package
        # -> SS Demand Tag. Both created paused; the operator (or a later
        # Stage 3 creative-bind call) flips them active.
        assert self._client is not None
        assert self.demand_partner_id is not None
        try:
            # SpringServe requires a numeric rate on campaign create. Pick the
            # first package's rate as a representative figure -- per-package
            # rates land on the demand_tag itself a few lines below.
            first_rate, _ = self._resolve_pricing_rate(packages[0], package_pricing_info) if packages else (0.0, "")
            campaign = self._client.campaigns.create(
                name=buy_name,
                demand_partner_id=int(self.demand_partner_id),
                is_active=False,
                code=request.po_number,
                secondary_code=f"adcp_{request.po_number}" if request.po_number else None,
                note=(
                    f"AdCP MediaBuy: po_number={request.po_number}, "
                    f"packages={len(packages)}, "
                    f"flight={start_time.date()}..{end_time.date()}"
                ),
                rate=first_rate,
                rate_currency=rate_currency,
            )
            for package in packages:
                rate, _ = self._resolve_pricing_rate(package, package_pricing_info)
                kwargs = self._demand_tag_kwargs(
                    package,
                    campaign_id=campaign.id,
                    rate=rate,
                    start_time=start_time,
                    end_time=end_time,
                    po_number=request.po_number,
                )
                created_tag = self._client.demand_tags.create(**kwargs)
                # KV / audience targeting goes through a separate sub-resource
                # POST per the SpringServe docs (page 1628471383). The parent
                # /demand_tags POST silently drops these fields if included in
                # the body. Tenant-level opt-in: most publishers express
                # audience/content through supply-tag selection, so KV writes
                # are off by default and have to be enabled explicitly.
                if self.enable_key_value_targeting:
                    kv_entries = build_demand_tag_kv_entries(package.targeting_overlay, tenant_id=self.tenant_id)
                    for entry in kv_entries:
                        self._post_kv_entry_or_raise(created_tag.id, entry)
        except SpringServeError as exc:
            logger.warning("SpringServe create_media_buy failed: %s body=%s", exc, exc.body)
            return CreateMediaBuyError(
                errors=[
                    Error(
                        code="upstream_error",
                        message=f"SpringServe rejected the request: {exc}",
                        details=None,
                    )
                ]
            )

        return self._build_create_success(request, f"springserve_{campaign.id}", packages)

    def _buy_name(self, request: CreateMediaBuyRequest) -> str:
        if request.po_number:
            return f"adcp_{request.po_number}"
        return f"adcp_{int(datetime.now(UTC).timestamp())}"

    # ----- creatives -----

    @staticmethod
    def _asset_media_type(asset: dict[str, Any]) -> tuple[str, str]:
        """Return ``(creative_format, creative_content_type)`` for an asset.

        Routing is driven by the AdCP Format id when present, falling back to
        the asset's own ``content_type`` hint, then to video/mp4 as a safe
        default.
        """
        format_ref = asset.get("format_id")
        fid = format_ref.get("id") if isinstance(format_ref, dict) else asset.get("format")
        content_type = str(asset.get("content_type", "")).lower()
        asset_type = str(asset.get("asset_type", "")).lower()
        fid_str = fid.lower() if isinstance(fid, str) else ""
        is_audio = ("audio" in fid_str) or asset_type == "audio" or content_type.startswith("audio/")
        if is_audio:
            return "audio", str(asset.get("content_type") or "audio/mpeg")
        return "video", str(asset.get("content_type") or "video/mp4")

    @staticmethod
    def _asset_remote_url(asset: dict[str, Any]) -> Any:
        """Return the remote media/VAST URL from supported adapter asset shapes."""
        return (
            asset.get("vast_tag")
            or asset.get("vast_tag_url")
            or asset.get("url")
            or asset.get("media_url")
            or asset.get("creative_remote_url")
        )

    @staticmethod
    def _hosted_audio_unsupported_status(creative_id: str) -> AssetStatus:
        return AssetStatus(
            creative_id=creative_id,
            status="failed",
            message=(
                "SpringServe hosted audio upload is not supported; configure demand_class=tag "
                "and use an audio_vast creative."
            ),
        )

    @staticmethod
    def _validate_creative_remote_url(url: str) -> str | None:
        """Return an error message if ``url`` isn't safe to forward to SpringServe,
        otherwise None.

        Defence-in-depth: SpringServe pulls the asset server-side, so an
        unconstrained URL is server-side request forgery on their network.
        We reject non-https schemes (``file://``, ``http://``, ``ftp://``,
        etc) and any URL whose hostname resolves to loopback or RFC1918
        private space before the POST.
        """
        import ipaddress
        import socket
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme.lower() != "https":
            return f"Asset URL must use https:// (got {parsed.scheme!r})"
        host = (parsed.hostname or "").lower()
        if not host:
            return "Asset URL is missing a hostname"

        def _blocked_ip_error(ip_text: str) -> str | None:
            try:
                ip = ipaddress.ip_address(ip_text)
            except ValueError:
                return None
            if not ip.is_global:
                return f"Asset URL host {host!r} resolves to non-public address {ip_text}"
            return None

        literal_error = _blocked_ip_error(host.strip("[]"))
        if literal_error:
            return literal_error

        try:
            addr_infos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            return f"Asset URL host {host!r} could not be resolved: {exc}"

        for *_, sockaddr in addr_infos:
            resolved_ip = sockaddr[0]
            resolved_error = _blocked_ip_error(str(resolved_ip))
            if resolved_error:
                return resolved_error
        return None

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """POST each hosted video asset to /videos and return AssetStatus.

        ``demand_class=tag`` tenants carry buyer-supplied VAST/audio URLs on
        the Demand Tag itself as ``vast_endpoint_url``. There is no separate
        creative upload or binding step for that path.

        Each asset is reported individually. A bad asset (missing
        ``creative_id``, missing URL, non-https URL, SpringServe rejection)
        produces a ``failed`` AssetStatus for that asset only -- the loop
        continues with the remaining assets.
        """
        if self.demand_class == "tag":
            tag_statuses: list[AssetStatus] = []
            for asset in assets:
                creative_id = str(asset.get("creative_id") or "")
                remote_url = self._asset_remote_url(asset)
                if not creative_id or not remote_url:
                    tag_statuses.append(AssetStatus(creative_id=creative_id, status="failed"))
                    continue
                url_error = self._validate_creative_remote_url(str(remote_url))
                tag_statuses.append(
                    AssetStatus(
                        creative_id=creative_id,
                        status="failed" if url_error else "approved",
                        message=url_error or "demand_class=tag carries the VAST URL on the demand tag",
                    )
                )
            return tag_statuses

        if self.dry_run:
            dry_run_statuses: list[AssetStatus] = []
            for asset in assets:
                media_format, content_type = self._asset_media_type(asset)
                creative_id = str(asset.get("creative_id") or "")
                if media_format == "audio":
                    dry_run_statuses.append(self._hosted_audio_unsupported_status(creative_id))
                    continue
                self.log(
                    f"Would POST {self.base_url}/videos name={asset.get('name')} "
                    f"format={media_format} content_type={content_type} "
                    f"remote_url={self._asset_remote_url(asset)}"
                )
                dry_run_statuses.append(AssetStatus(creative_id=creative_id, status="approved"))
            return dry_run_statuses

        assert self._client is not None
        assert self.demand_partner_id is not None
        live_statuses: list[AssetStatus] = []
        for asset in assets:
            creative_id = str(asset.get("creative_id") or "")
            if not creative_id:
                logger.warning("SpringServe asset missing 'creative_id'; skipping")
                live_statuses.append(AssetStatus(creative_id="", status="failed"))
                continue
            remote_url = self._asset_remote_url(asset)
            if not remote_url:
                logger.warning("SpringServe asset %s missing remote URL (need 'url' or 'media_url')", creative_id)
                live_statuses.append(AssetStatus(creative_id=creative_id, status="failed"))
                continue
            media_format, content_type = self._asset_media_type(asset)
            if media_format == "audio":
                live_statuses.append(self._hosted_audio_unsupported_status(creative_id))
                continue
            url_error = self._validate_creative_remote_url(str(remote_url))
            if url_error:
                logger.warning("SpringServe asset %s rejected: %s", creative_id, url_error)
                live_statuses.append(AssetStatus(creative_id=creative_id, status="failed"))
                continue
            try:
                created = self._client.creatives.create(
                    name=asset.get("name") or f"adcp-{creative_id}",
                    demand_partner_id=int(self.demand_partner_id),
                    creative_remote_url=str(remote_url),
                    creative_format=media_format,
                    creative_content_type=content_type,
                    duration_seconds=asset.get("duration_seconds"),
                    width=asset.get("width"),
                    height=asset.get("height"),
                    creative_landing_page_url=asset.get("landing_page_url"),
                    secondary_code=creative_id,
                )
                live_statuses.append(AssetStatus(creative_id=str(created.id), status="approved"))
            except SpringServeError as exc:
                logger.warning("SpringServe creative create failed for asset %s: %s", creative_id, exc)
                live_statuses.append(AssetStatus(creative_id=creative_id, status="failed"))
        return live_statuses

    def associate_creatives(self, line_item_ids: list[str], platform_creative_ids: list[str]) -> list[dict[str, Any]]:
        """Bind SpringServe creatives to demand tags.

        SpringServe's Line Item demand class stores hosted creative rotation
        in ``line_item_ratios``; writing ``creative_id`` to this class is
        accepted by the API but ignored. Stage 3 still wires only one creative
        per demand tag; if multiple creative_ids are supplied for the same
        demand tag, the LAST one wins and earlier ones are recorded as
        ``skipped``. The tag is flipped active on a successful bind so it can
        deliver.

        When ``demand_class="tag"`` the demand tag is a passthrough to a
        third-party VAST/audio URL and has no Creatives surface, so
        creative binding is a no-op -- the buyer's tag URL is the
        creative and the bind step is skipped with status ``skipped``.
        """
        if not platform_creative_ids:
            return []
        if self.demand_class == "tag":
            return [
                {
                    "line_item_id": li,
                    "creative_id": ci,
                    "status": "skipped",
                    "message": (
                        "demand_class=tag -- the third-party VAST/audio URL on the "
                        "demand tag is the creative; no separate binding is needed."
                    ),
                }
                for li in line_item_ids
                for ci in platform_creative_ids
            ]
        winner = platform_creative_ids[-1]
        losers = platform_creative_ids[:-1]
        results: list[dict[str, Any]] = []
        for li in line_item_ids:
            results.extend(self._skip_extra_creative_result(li, ci) for ci in losers)
            results.append(self._bind_creative_to_demand_tag(li, winner))
        return results

    # The specific 422 SpringServe returns when the parent demand_tag's
    # ``key_value_targeting`` flag isn't set. Tenants who turn KV on need
    # the publisher's SpringServe account configured to allow that flag
    # on demand tags they own; if it isn't, the entry POST fails with
    # this body and we surface it as a warning rather than failing the
    # whole buy. Different 422s (bad key_id, malformed values, etc) still
    # propagate so the buyer sees the real error.
    _KV_PARENT_FLAG_BLOCKER_TOKENS = ("key_value_targeting set to true",)

    def _post_kv_entry_or_raise(self, demand_tag_id: int, entry: dict[str, Any]) -> None:
        """POST one KV-targeting entry; tolerate ONLY the parent-flag 422.

        Anything else (5xx, 401, 429, a different 422) re-raises so the
        outer ``create_media_buy`` error path surfaces it to the buyer.
        The earlier blanket-catch was masking real failures as if they
        were the documented blocker.
        """
        assert self._client is not None
        try:
            self._client.demand_tags.add_kv_entry(demand_tag_id, **entry)
            return
        except SpringServeValidationError as exc:
            body = (exc.body or "").lower()
            if not any(tok in body for tok in self._KV_PARENT_FLAG_BLOCKER_TOKENS):
                # Different 422 -- not the parent-flag blocker. Let it propagate.
                raise
            logger.warning(
                "SpringServe KV entry rejected for demand_tag %s key_id=%s "
                "(parent demand_tag.key_value_targeting=false; targeting will not apply)",
                demand_tag_id,
                entry.get("key_id"),
            )

    def _skip_extra_creative_result(self, line_item_id: str, creative_id: str) -> dict[str, Any]:
        if self.dry_run:
            self.log(f"Would skip extra creative={creative_id} on demand_tag={line_item_id} (only last wins)")
        return {
            "line_item_id": line_item_id,
            "creative_id": creative_id,
            "status": "skipped",
            "message": "Multiple creatives per demand tag -- only the last is wired in Stage 3.",
        }

    def _bind_creative_to_demand_tag(self, line_item_id: str, creative_id: str) -> dict[str, Any]:
        if self.dry_run:
            self.log(
                f"Would PUT .../demand_tags/{line_item_id} "
                f"line_item_ratios=[{{creative_id: {creative_id}, ratio: 1}}] is_active=true"
            )
            return {"line_item_id": line_item_id, "creative_id": creative_id, "status": "success"}
        assert self._client is not None
        try:
            self._client.demand_tags.update(
                int(line_item_id),
                line_item_ratios=[{"creative_id": int(creative_id), "ratio": 1}],
                is_active=True,
            )
        except SpringServeError as exc:
            logger.warning("SpringServe bind creative=%s -> demand_tag=%s failed: %s", creative_id, line_item_id, exc)
            return {
                "line_item_id": line_item_id,
                "creative_id": creative_id,
                "status": "failed",
                "message": str(exc),
            }
        return {"line_item_id": line_item_id, "creative_id": creative_id, "status": "success"}

    # ----- status / delivery -----

    def check_media_buy_status(self, media_buy_id: str, today: datetime) -> CheckMediaBuyStatusResponse:
        campaign_id = media_buy_id.removeprefix("springserve_")
        if self.dry_run:
            self.log(f"Would call: GET {self.base_url}/campaigns/{campaign_id}")
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="active")
        assert self._client is not None
        try:
            campaign = self._client.campaigns.get(int(campaign_id))
        except SpringServeError as exc:
            logger.warning("SpringServe get_campaign(%s) failed: %s", campaign_id, exc)
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="unknown")
        # SpringServe carries the on/off state on ``is_active``; map to the
        # AdCP buyer-facing status enum.
        status = "active" if campaign.is_active else "paused"
        return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status=status)

    def get_media_buy_delivery(
        self, media_buy_id: str, date_range: ReportingPeriod, today: datetime
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Aggregate delivery totals from the demand-tag-stats cache.

        Live mode reads ``springserve_demand_tag_stats`` (populated by the
        reporting sync). When the cache is empty (sync not running yet, or
        scope still pending) raises :class:`DeliveryDataUnavailable` so
        the impl layer surfaces a ``data_unavailable`` error rather than
        a fabricated zero-delivery response. Matches the FreeWheel adapter
        contract.

        Dry-run returns simulated numbers for demo/testing.
        """
        if self.dry_run:
            return self._simulated_delivery_response(
                media_buy_id,
                date_range,
                today,
                target_impressions=500_000,
                cpm=15.0,
                completion_rate=0.85,
            )
        campaign_id = media_buy_id.removeprefix("springserve_")
        with get_db_session() as session:
            repo = SpringServeDemandTagStatsRepository(session, self.tenant_id)
            stats_rows = repo.list_by_campaign(campaign_id)
        if not stats_rows:
            raise DeliveryDataUnavailable(media_buy_id)
        return self._aggregate_stat_rows_to_delivery_response(
            media_buy_id,
            date_range,
            stats_rows,
            package_id_attr="demand_tag_id",
        )

    def get_packages_snapshot(
        self, package_refs: list[tuple[str, str, str | None]]
    ) -> dict[str, dict[str, Snapshot | None]]:
        """Near-real-time package snapshots from the demand-tag-stats cache.

        Returns one ``Snapshot`` per package. Missing rows (no reporting
        sync data yet, or no scope granted) surface as ``None`` so the
        caller can render a 'no data' state rather than failing.
        """
        now = datetime.now(UTC)
        result: dict[str, dict[str, Snapshot | None]] = {}
        demand_tag_ids = [ref[2] for ref in package_refs if ref[2] is not None]
        if not demand_tag_ids:
            for media_buy_id, package_id, _ in package_refs:
                result.setdefault(media_buy_id, {})[package_id] = None
            return result
        with get_db_session() as session:
            repo = SpringServeDemandTagStatsRepository(session, self.tenant_id)
            stats = repo.get_by_demand_tag_ids(demand_tag_ids)
        for media_buy_id, package_id, demand_tag_id in package_refs:
            row = stats.get(demand_tag_id) if demand_tag_id else None
            result.setdefault(media_buy_id, {})[package_id] = self._snapshot_from_stat_row(row, now)
        return result

    def _snapshot_from_stat_row(self, row: Any, now: datetime) -> Snapshot | None:
        """Convert a SpringServeDemandTagStats row into an AdCP Snapshot."""
        if row is None:
            return None
        as_of = row.as_of if row.as_of.tzinfo else row.as_of.replace(tzinfo=UTC)
        return Snapshot(
            as_of=as_of,
            impressions=float(row.impressions or 0),
            spend=(row.spend_micros or 0) / 1_000_000.0,
            clicks=float(row.clicks) if row.clicks is not None else None,
            staleness_seconds=max(0, int((now - as_of).total_seconds())),
            delivery_status=self._platform_status_to_delivery_status(row.delivery_status),
            currency=row.currency,
        )

    def run_reporting_sync(
        self,
        *,
        demand_tag_ids: list[str] | None = None,
        start_date: Any = None,
        end_date: Any = None,
    ) -> AdapterSyncResult:
        """Refresh the SpringServe demand-tag-stats cache via the Reporting API.

        Today this raises :class:`ReportingScopeNotGranted` on its first
        call against most accounts. The shared ``_wrap_sync_run`` helper
        translates that into a soft-failed :class:`AdapterSyncResult` so
        the scheduler keeps retrying without log spam.
        """
        from src.adapters.springserve._reporting import ReportingError
        from src.adapters.springserve.reporting_sync import (
            ReportingScopeNotGranted,
            SpringServeReportingSync,
        )

        if self.dry_run or self._client is None:
            return AdapterSyncResult(
                sync_kind="reporting",
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                succeeded=False,
                errors={"adapter": "dry-run mode -- no live client to sync with"},
            )

        assert self._client is not None  # narrowed by the early-return above
        client = self._client

        def _do_sync() -> Any:
            with get_db_session() as session:
                syncer = SpringServeReportingSync(
                    client=client,
                    tenant_id=self.tenant_id,
                    session=session,
                )
                return syncer.run(
                    start_date=start_date,
                    end_date=end_date,
                    demand_tag_ids=demand_tag_ids,
                )

        return self._wrap_sync_run(
            "reporting",
            _do_sync,
            scope_error_types=(ReportingScopeNotGranted,),
            retryable_error_types=(ReportingError,),
            rows_count_key="demand_tags",
        )

    def latest_reporting_sync_at(self) -> datetime | None:
        """Most-recent ``last_synced_at`` across cached demand-tag stats."""
        with get_db_session() as session:
            return SpringServeDemandTagStatsRepository(session, self.tenant_id).latest_sync_at()

    def run_inventory_sync(self) -> AdapterSyncResult:
        """Refresh the SpringServe inventory cache from the API.

        Wraps :class:`SpringServeInventorySync`. Today the supply-side
        reads return 403; the shared ``_wrap_sync_run`` helper translates
        ``SupplyScopeNotGranted`` into a soft-failed result with
        ``scope_pending`` metadata so the scheduler keeps trying.
        """
        from src.adapters.springserve.inventory_sync import (
            SpringServeInventorySync,
            SupplyScopeNotGranted,
        )

        if self.dry_run or self._client is None:
            return AdapterSyncResult(
                sync_kind="inventory",
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                succeeded=False,
                errors={"adapter": "dry-run mode -- no live client to sync with"},
            )

        assert self._client is not None
        client = self._client

        def _do_sync() -> Any:
            with get_db_session() as session:
                syncer = SpringServeInventorySync(
                    client=client,
                    tenant_id=self.tenant_id,
                    session=session,
                )
                return syncer.run()

        return self._wrap_sync_run(
            "inventory",
            _do_sync,
            scope_error_types=(SupplyScopeNotGranted,),
            rows_count_key="entities",
        )

    def latest_inventory_sync_at(self) -> datetime | None:
        """Most-recent ``last_synced_at`` across cached inventory rows."""
        with get_db_session() as session:
            return SpringServeInventoryRepository(session, self.tenant_id).latest_sync_at()

    async def get_available_inventory(self) -> dict[str, Any]:
        """Surface the locally-synced SpringServe taxonomy for product config.

        Reads from the ``springserve_inventory`` cache. No SpringServe API
        calls happen here -- everything is served from local cache so the
        AI product configurator runs offline.

        Shape follows the base ``get_available_inventory`` contract:

        * ``placements`` -- SpringServe supply tags (the per-property inventory units)
        * ``ad_units`` -- supply partners (the business-unit grouping)
        * ``targeting_options`` -- empty for Stage 5 (refined when the schema
          is observed against a live account with scope granted)
        * ``creative_specs`` -- the static VAST format declarations
        * ``properties`` -- cache counts and metadata
        """
        with get_db_session() as session:
            repo = SpringServeInventoryRepository(session, self.tenant_id)
            supply_partners = repo.list_by_type("supply_partner")
            supply_tags = repo.list_by_type("supply_tag")

            placements = [
                {
                    "id": f"supply_tag:{row.entity_id}",
                    "name": row.name or row.entity_id,
                    "type": "supply_tag",
                    "parent": row.supply_router_id or row.supply_partner_id,
                }
                for row in supply_tags
            ]
            ad_units = [
                {
                    "path": f"supply_partner:{row.entity_id}",
                    "name": row.name or row.entity_id,
                    "type": "supply_partner",
                }
                for row in supply_partners
            ]
            properties = {
                "supply_partners_count": len(supply_partners),
                "supply_tags_count": len(supply_tags),
            }

        return {
            "placements": placements,
            "ad_units": ad_units,
            "targeting_options": {},
            "creative_specs": springserve_creative_formats(self.tenant_id),
            "properties": properties,
        }

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
            campaign_id = media_buy_id.removeprefix("springserve_")
            if action == "pause_media_buy":
                self.log(f"Would PUT .../campaigns/{campaign_id} active=false")
            elif action == "resume_media_buy":
                self.log(f"Would PUT .../campaigns/{campaign_id} active=true")
            elif action in {"pause_package", "resume_package"} and package_id:
                self.log(f"Would PUT demand_tag external_id={package_id} active=<bool>")
            elif action in {"update_package_budget", "update_package_impressions"} and package_id and budget:
                self.log(f"Would PUT demand_tag external_id={package_id} goal={budget}")
            return UpdateMediaBuySuccess(media_buy_id=media_buy_id, affected_packages=[], implementation_date=today)

        # Live mode -- map AdCP actions to Campaign / Demand Tag PUTs.
        assert self._client is not None
        campaign_id = media_buy_id.removeprefix("springserve_")
        try:
            if action == "pause_media_buy":
                self._client.campaigns.update(int(campaign_id), is_active=False)
            elif action == "resume_media_buy":
                self._client.campaigns.update(int(campaign_id), is_active=True)
            elif action in {"pause_package", "resume_package"} and package_id:
                dt_id = self._find_demand_tag_id(int(campaign_id), package_id)
                if dt_id is None:
                    return self._package_not_found_error(package_id)
                self._client.demand_tags.update(dt_id, is_active=(action == "resume_package"))
            elif action in {"update_package_budget", "update_package_impressions"} and package_id and budget:
                # Budget on the demand tag is encoded via the ``budgets`` list
                # of nested objects. Surfaced in Stage 4 alongside reporting;
                # rejected here so callers see an honest error instead of a
                # silent no-op.
                return self._unsupported_action_error(f"{action} (pending Stage 4)")
            else:
                return self._unsupported_action_error(action)
        except SpringServeError as exc:
            logger.warning("SpringServe update_media_buy failed: %s body=%s", exc, exc.body)
            return UpdateMediaBuyError(
                errors=[
                    Error(
                        code="upstream_error",
                        message=f"SpringServe rejected the update: {exc}",
                        details=None,
                    )
                ]
            )
        from src.core.schemas import AffectedPackage

        affected = [AffectedPackage(package_id=package_id)] if package_id else []
        return UpdateMediaBuySuccess(
            media_buy_id=media_buy_id,
            affected_packages=affected,
            implementation_date=today,
        )

    def _find_demand_tag_id(self, campaign_id: int, package_id: str) -> int | None:
        """Look up the SpringServe demand_tag.id for an AdCP package_id.

        The demand_tag's ``secondary_code`` is set to the AdCP package_id
        at creation time, so we can find it by scanning the campaign's
        demand_tag_ids. SpringServe's per-campaign demand-tag list isn't a
        free filter on the docs, so we fetch each by id; in practice
        campaigns have at most a handful of demand tags so the round-trip
        cost is low.
        """
        assert self._client is not None
        try:
            campaign = self._client.campaigns.get(campaign_id)
        except SpringServeError:
            return None
        for dt_id in campaign.demand_tag_ids:
            try:
                tag = self._client.demand_tags.get(dt_id)
            except SpringServeError:
                continue
            if tag.secondary_code == package_id:
                return tag.id
        return None

    def _package_not_found_error(self, package_id: str) -> UpdateMediaBuyError:
        return UpdateMediaBuyError(
            errors=[
                Error(
                    code="package_not_found",
                    message=f"No SpringServe demand tag found for package_id={package_id!r}",
                    details=None,
                )
            ]
        )
