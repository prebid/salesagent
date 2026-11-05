"""Account-scoped simulated execution with the configured adapter's validation."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.adapters.base import (
    AdapterCapabilities,
    AdapterCreateRequest,
    AdapterCreateResult,
    AdapterUpdateResult,
    AdServerAdapter,
)
from src.core.database.repositories.media_buy import SandboxBuyState, read_sandbox_buy_state
from src.core.errors.details import EntityRefDetails, ErrorProblem
from src.core.exceptions import AdCPMediaBuyNotFoundError
from src.core.resolved_identity import AccountIdentity
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    AssetStatus,
    CheckMediaBuyStatusResponse,
    DeliveryStatus,
    DeliveryTotals,
    MediaPackage,
    ReportingPeriod,
    Snapshot,
)


class SandboxAdServerAdapter(AdServerAdapter):
    """Never owns a vendor instance, credentials or process-global simulated state."""

    adapter_name = "sandbox"
    capabilities = AdapterCapabilities(supports_realtime_reporting=True)

    def __init__(
        self,
        identity: AccountIdentity,
        *,
        configured_adapter_type: str,
        validation_class: type[AdServerAdapter],
        manual_approval_required: bool | None,
    ) -> None:
        super().__init__(
            {"manual_approval_required": manual_approval_required},
            identity.principal,
            tenant_id=identity.tenant_id,
        )
        self.account_id = identity.account.account_id
        self.configured_adapter_type = configured_adapter_type
        self.validation_class = validation_class

    def validate_media_buy_request(
        self,
        request: AdapterCreateRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> list[ErrorProblem]:
        return self.validation_class.validate_request(
            request,
            packages,
            start_time,
            end_time,
            package_pricing_info,
        )

    def create_media_buy(
        self,
        request: AdapterCreateRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> AdapterCreateResult:
        return self._build_create_success(
            f"sandbox_{uuid4().hex}",
            packages,
            platform_line_item_ids={package.package_id: f"sandbox_line_{uuid4().hex}" for package in packages},
        )

    def add_creative_assets(
        self,
        media_buy_id: str,
        assets: list[dict[str, Any]],
        today: datetime,
    ) -> list[AssetStatus]:
        # Callers own buy/assignment authorization and persistence, including inside a UoW.
        return [
            AssetStatus(asset_id=str(asset["id"]), creative_id=f"sandbox_creative_{uuid4().hex}", status="approved")
            for asset in assets
        ]

    def associate_creatives(self, line_item_ids: list[str], platform_creative_ids: list[str]) -> list[dict[str, Any]]:
        return [
            {"line_item_id": line_item_id, "creative_id": creative_id, "status": "success"}
            for line_item_id in line_item_ids
            for creative_id in platform_creative_ids
        ]

    def _state(self, media_buy_id: str) -> SandboxBuyState:
        state = read_sandbox_buy_state(self.tenant_id, self.principal_id, self.account_id, media_buy_id)
        if state is None:
            raise AdCPMediaBuyNotFoundError(details=EntityRefDetails(media_buy_id=media_buy_id))
        return state

    def check_media_buy_status(self, media_buy_id: str, today: datetime) -> CheckMediaBuyStatusResponse:
        return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status=self._state(media_buy_id).status)

    def get_media_buy_delivery(
        self,
        media_buy_id: str,
        date_range: ReportingPeriod,
        today: datetime,
    ) -> AdapterGetMediaBuyDeliveryResponse:
        state = self._state(media_buy_id)
        if state.seeded_delivery is not None:
            return AdapterGetMediaBuyDeliveryResponse.model_validate(state.seeded_delivery)
        # Without seeded measurements the stored buy has no delivered events.
        return AdapterGetMediaBuyDeliveryResponse(
            media_buy_id=media_buy_id,
            reporting_period=date_range,
            totals=DeliveryTotals(impressions=0, spend=0),
            currency=state.currency,
            by_package=[
                AdapterPackageDelivery(package_id=package_id, impressions=0, spend=0)
                for package_id in state.package_ids
            ],
        )

    def get_packages_snapshot(
        self,
        package_refs: list[tuple[str, str, str | None]],
    ) -> dict[str, dict[str, Snapshot | None]]:
        result: dict[str, dict[str, Snapshot | None]] = {}
        for media_buy_id, package_id, _ in package_refs:
            state = read_sandbox_buy_state(self.tenant_id, self.principal_id, self.account_id, media_buy_id)
            snapshot = None
            if state is not None and package_id in state.package_ids:
                impressions, spend = 0, 0.0
                if state.seeded_delivery is not None:
                    delivery = AdapterGetMediaBuyDeliveryResponse.model_validate(state.seeded_delivery)
                    metrics = next(
                        (package for package in delivery.by_package if package.package_id == package_id), None
                    )
                    if metrics is not None:
                        impressions, spend = metrics.impressions, metrics.spend
                snapshot = Snapshot(
                    as_of=datetime.now(UTC),
                    impressions=impressions,
                    spend=spend,
                    delivery_status=DeliveryStatus.delivering if impressions else DeliveryStatus.not_delivering,
                    staleness_seconds=0,
                )
            result.setdefault(media_buy_id, {})[package_id] = snapshot
        return result

    def update_media_buy(
        self,
        media_buy_id: str,
        action: str,
        package_id: str | None,
        budget: int | None,
        today: datetime,
    ) -> AdapterUpdateResult:
        # The controller's current UoW owns all domain writes. Opening another here
        # would close the thread-scoped caller session and lose its pending changes.
        return AdapterUpdateResult(media_buy_id=media_buy_id, affected_packages=[])
