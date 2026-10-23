"""Real factory state for internal sandbox execution contracts."""

from typing import Any

from tests.factories import AdapterConfigFactory
from tests.harness._base import IntegrationEnv
from tests.helpers.account_seeding import seed_account_with_access


class SandboxAdapterEnv(IntegrationEnv):
    """Keep identity and repositories real; double Google SDK boundaries."""

    EXTERNAL_PATCHES = {
        "gam_client": "src.adapters.gam.client.ad_manager.AdManagerClient",
        "gam_oauth": "src.adapters.gam.auth.oauth2.GoogleRefreshTokenClient",
    }

    def seed_configuration(
        self,
        adapter_type: str = "google_ad_manager",
        approval: bool | None = True,
        *,
        tenant_adapter: str = "mock",
        credentials: bool = False,
        configured: bool = True,
    ) -> tuple[str, str]:
        tenant, principal = self.setup_default_data(ad_server=tenant_adapter, human_review_required=False)
        for account_id, sandbox in (("sandbox", True), ("live", False), ("sibling", True)):
            seed_account_with_access(tenant, principal, account_id=account_id, sandbox=sandbox)
        if configured:
            row = AdapterConfigFactory(
                tenant=tenant,
                adapter_type=adapter_type,
                gam_network_code="12345" if credentials else None,
                gam_refresh_token="test-refresh-token" if credentials else None,
                gam_trafficker_id="67890" if credentials else None,
            )
            for column in (
                "gam_manual_approval_required",
                "mock_manual_approval_required",
                "kevel_manual_approval_required",
            ):
                setattr(row, column, approval)
            self._commit_factory_data()
        return tenant.tenant_id, principal.principal_id

    def stored_buy(self, *, media_buy_id: str = "stored", account_id: str = "sandbox", **kwargs: Any) -> None:
        from tests.factories import MediaBuyFactory, MediaPackageFactory
        from tests.factories.media_buy import request_package

        tenant, principal = self.setup_default_data()
        buy = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id=media_buy_id,
            account_id=account_id,
            raw_request={"packages": [request_package(package_id="package", budget=100)]},
            **kwargs,
        )
        MediaPackageFactory(media_buy=buy, package_id="package", budget=100)

    def seed_delivery(self, media_buy_id: str = "stored") -> None:
        from tests.factories import DeliverySimulationConfigFactory

        tenant, _ = self.setup_default_data()
        DeliverySimulationConfigFactory(
            tenant=tenant,
            media_buy_id=media_buy_id,
            response_payload={
                "media_buy_id": media_buy_id,
                "reporting_period": {"start": "2026-10-01T00:00:00Z", "end": "2026-10-10T00:00:00Z"},
                "totals": {"impressions": 4321, "spend": 43.21},
                "by_package": [{"package_id": "package", "impressions": 4321, "spend": 43.21}],
                "currency": "USD",
            },
        )

    def persist_through_adapter_reads(self, identity: Any) -> str:
        from datetime import UTC, datetime

        from src.core.database.models import MediaBuy
        from src.core.database.repositories.uow import MediaBuyUoW
        from src.core.helpers.adapter_helpers import get_adapter
        from src.core.schemas import ReportingPeriod

        with MediaBuyUoW(identity.tenant_id) as uow:
            buy = uow.media_buys.get_by_id("stored")
            buy.order_name = "Survived adapter reads"
            adapter = get_adapter(identity)
            adapter.get_media_buy_delivery(
                "stored",
                ReportingPeriod(start="2026-10-01T00:00:00Z", end="2026-10-10T00:00:00Z"),
                datetime.now(UTC),
            )
        self.get_session().expire_all()
        return self.get_one(MediaBuy, media_buy_id="stored").order_name

    def package_budget(self, media_buy_id: str, package_id: str) -> float:
        from src.core.database.models import MediaPackage

        self.get_session().expire_all()
        return float(self.get_one(MediaPackage, media_buy_id=media_buy_id, package_id=package_id).budget)

    def observe_configuration_reads(self) -> list[str]:
        """Observe real SQL columns without replacing the repository or credential builder."""
        from sqlalchemy import event

        from src.core.database.database_session import get_engine

        statements: list[str] = []

        def record(_connection: Any, _cursor: Any, statement: str, _parameters: Any, _context: Any, _many: Any) -> None:
            if statement.lstrip().upper().startswith("SELECT") and "adapter_config" in statement:
                statements.append(statement)

        engine = get_engine()
        event.listen(engine, "before_cursor_execute", record)
        self._guard("configuration SQL observation", lambda: event.remove(engine, "before_cursor_execute", record))
        return statements
