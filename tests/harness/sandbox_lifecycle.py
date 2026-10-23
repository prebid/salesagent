"""Factory-backed lifecycle setup with production adapters and approval gates."""

from contextlib import contextmanager
from typing import Any

from tests.harness._mixins import LocalOriginMixin, WebhookOutcomeRowsMixin
from tests.harness._realize import realize_e2e
from tests.harness.media_buy_create_update_list import MediaBuyCreateUpdateListEnv
from tests.harness.sandbox_adapter import SandboxAdapterEnv


class SandboxLifecycleEnv(MediaBuyCreateUpdateListEnv, SandboxAdapterEnv):
    """Reuse dispatch only; all seller services run without internal patches."""

    EXTERNAL_PATCHES = SandboxAdapterEnv.EXTERNAL_PATCHES

    def _configure_mocks(self) -> None:
        self.mock["gam_client"].side_effect = AssertionError("sandbox constructed the Google client")
        self.mock["gam_oauth"].side_effect = AssertionError("sandbox assembled Google credentials")

    def seed_lifecycle(
        self,
        *,
        approval: bool = False,
        adapter_type: str = "mock",
        tenant_approval: bool | None = None,
        adapter_approval: bool | None = None,
    ) -> None:
        from tests.factories import AdapterConfigFactory
        from tests.helpers.account_seeding import seed_account_with_access

        tenant, principal, product, _ = self.setup_media_buy_data()
        tenant.ad_server = adapter_type
        tenant.human_review_required = approval if tenant_approval is None else tenant_approval
        product.format_ids = [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"}]
        for account_id, sandbox in (("sandbox", True), ("sibling", True), ("live", False)):
            seed_account_with_access(tenant, principal, account_id=account_id, sandbox=sandbox)
        AdapterConfigFactory(
            tenant=tenant,
            adapter_type=adapter_type,
            mock_manual_approval_required=approval if adapter_approval is None else adapter_approval,
            kevel_manual_approval_required=approval if adapter_approval is None else adapter_approval,
            gam_manual_approval_required=approval if adapter_approval is None else adapter_approval,
            gam_network_code="12345",
            gam_refresh_token="test-token",
            gam_trafficker_id="67890",
        )
        if adapter_type == "google_ad_manager":
            from tests.factories import GAMInventoryFactory

            GAMInventoryFactory(tenant=tenant)
        self._commit_factory_data()

    def create_request(self, account_id: str = "sandbox", **kwargs: Any) -> Any:
        from tests.factories.request import CreateMediaBuyRequestFactory, PackageRequestFactory

        kwargs.setdefault("packages", [PackageRequestFactory(product_id="prod_1")])
        return CreateMediaBuyRequestFactory(account={"account_id": account_id}, **kwargs)

    def seed_owned_buy(
        self,
        account_id: str | None,
        *,
        media_buy_id: str = "owned-buy",
        package_paused: bool = False,
        package_budget: float = 5000,
        **kwargs: Any,
    ) -> str:
        from tests.factories import MediaBuyFactory, MediaPackageFactory
        from tests.factories.media_buy import package_config_for, request_package

        tenant, principal = self.setup_default_data()
        package = request_package(package_id="owned-package", product_id="prod_1", budget=package_budget)
        buy = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            account_id=account_id,
            media_buy_id=media_buy_id,
            raw_request={"packages": [package]},
            **kwargs,
        )
        MediaPackageFactory(
            media_buy=buy,
            package_id="owned-package",
            budget=package["budget"],
            package_config={**package_config_for(package), "paused": package_paused},
        )
        self._commit_factory_data()
        return buy.media_buy_id

    def seed_live_reporting_buy(self, transport) -> str:
        result = self.call_via(transport, req=self.create_request("live", start_time="asap"))
        return result.require_wire()["media_buy_id"]

    def buy_state(self, media_buy_id: str) -> tuple[str, bool, int]:
        from src.core.database.repositories.uow import MediaBuyUoW

        with MediaBuyUoW(self.tenant_id) as uow:
            buy = uow.media_buys.get_by_id(media_buy_id)
            return buy.status, buy.is_paused, buy.revision

    def vendor_calls(self) -> int:
        return self.mock["gam_client"].call_count + self.mock["gam_oauth"].call_count

    def package_state(self, media_buy_id: str) -> tuple[float, dict]:
        from src.core.database.repositories.uow import MediaBuyUoW

        with MediaBuyUoW(self.tenant_id) as uow:
            package = uow.media_buys.get_package_or_raise(media_buy_id, "owned-package")
            return float(package.budget), dict(package.package_config)

    def update_request(self, account_id: str, media_buy_id: str, action: str) -> Any:
        from tests.factories.request import UpdateMediaBuyRequestFactory

        if action.startswith("campaign_"):
            changes = {"paused": action == "campaign_pause"}
        else:
            operation = action.removesuffix("_targeting")
            package_changes = {"budget": 6000} if operation == "budget" else {"paused": operation == "pause"}
            if action.endswith("_targeting"):
                package_changes["targeting_overlay"] = {"geo_countries": ["US"]}
            changes = {"packages": [{"package_id": "owned-package", **package_changes}]}
        return UpdateMediaBuyRequestFactory(account={"account_id": account_id}, media_buy_id=media_buy_id, **changes)

    def request_with_targeting(self, account_id: str, field: str, value: str) -> Any:
        from tests.factories.request import PackageRequestFactory

        return self.create_request(
            account_id, packages=[PackageRequestFactory(product_id="prod_1", targeting_overlay={field: [value]})]
        )

    def seed_pricing_request(self, pricing_model: str) -> Any:
        from tests.factories import PricingOptionFactory
        from tests.factories.request import PackageRequestFactory

        option = PricingOptionFactory(tenant_id=self.tenant_id, product_id="prod_1", pricing_model=pricing_model)
        option_id = option.pricing_option_id
        self._commit_factory_data()
        return self.create_request(packages=[PackageRequestFactory(product_id="prod_1", pricing_option_id=option_id)])

    def submitted_buy_id(self) -> str:
        from src.core.database.models import MediaBuy

        return self.get_one(MediaBuy, tenant_id=self.tenant_id).media_buy_id

    def execution_state(self, media_buy_id: str) -> tuple[str, Any, list[dict]]:
        from src.core.database.repositories.uow import MediaBuyUoW

        with MediaBuyUoW(self.tenant_id) as uow:
            buy = uow.media_buys.get_by_id_or_raise(media_buy_id)
            return (
                buy.status,
                buy.confirmed_at,
                [dict(package.package_config) for package in uow.media_buys.get_packages(media_buy_id)],
            )

    def _persist_product_policy(self, config: dict | None) -> None:
        from src.core.database.models import Product

        product = self.get_one(Product, tenant_id=self.tenant_id, product_id="prod_1")
        product.implementation_config = config
        self._commit_factory_data()

    @realize_e2e(_persist_product_policy)
    def set_product_policy(self, config: dict | None) -> None:
        """Commit the same policy to the env's database on local and live REST paths."""
        self._persist_product_policy(config)

    def product_policy(self) -> dict | None:
        from src.core.database.models import Product

        self.get_session().expire_all()
        return self.get_one(Product, tenant_id=self.tenant_id, product_id="prod_1").implementation_config

    def seed_shared_creative(self, *, pending_buy: bool = False) -> tuple[str, str]:
        from datetime import UTC, datetime, timedelta

        from src.core.context_manager import ContextManager
        from tests.factories import CreativeAssignmentFactory, CreativeFactory, MediaBuyFactory, MediaPackageFactory
        from tests.factories.media_buy import package_config_for, request_package
        from tests.helpers.media_buy_approval import uploadable_creative

        tenant, principal = self.setup_default_data()
        creative = uploadable_creative(CreativeFactory, tenant=tenant, principal=principal, status="approved")
        creative.data = {**creative.data, "platform_creative_id": "live-creative-123"}
        start = datetime.now(UTC) + timedelta(days=2)
        end = start + timedelta(days=30)
        request = self.create_request(start_time=start, end_time=end)
        package = request_package(package_id="creative-package", product_id="prod_1")
        for account_id in ("sandbox", "live"):
            buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                account_id=account_id,
                media_buy_id=f"{account_id}-creative-buy",
                status="pending_approval" if pending_buy else "active",
                start_time=start,
                end_time=end,
                start_date=start.date(),
                end_date=end.date(),
                raw_request=request.model_dump(mode="json"),
            )
            MediaPackageFactory(
                media_buy=buy,
                package_id="creative-package",
                budget=5000,
                package_config={**package_config_for(package), "platform_order_id": f"{account_id}-order"},
            )
            CreativeAssignmentFactory(creative=creative, media_buy=buy, package_id="creative-package")
        creative_id = creative.creative_id
        self._commit_factory_data()
        manager = ContextManager()
        context = manager.create_context(tenant_id=self.tenant_id, principal_id=self._principal_id)
        step = manager.create_workflow_step(
            context_id=context.context_id,
            tool_name="create_media_buy",
            step_type="approval",
            owner="publisher",
            status="requires_approval",
            request_data={},
            object_mappings=[{"object_type": "media_buy", "object_id": "sandbox-creative-buy", "action": "approve"}],
        )
        return creative_id, step.step_id

    def creative_persistence(self, creative_id: str) -> tuple[dict, dict, dict]:
        from src.core.database.repositories.uow import MediaBuyUoW

        with MediaBuyUoW(self.tenant_id) as uow:
            creative = uow.creatives.get_by_id(creative_id, self._principal_id)
            sandbox_package = uow.media_buys.get_package("sandbox-creative-buy", "creative-package")
            live_package = uow.media_buys.get_package("live-creative-buy", "creative-package")
            return dict(creative.data), dict(sandbox_package.package_config), dict(live_package.package_config)

    def workflow_status(self, step_id: str) -> str:
        from src.core.database.models import WorkflowStep

        self.get_session().expire_all()
        return self.get_one(WorkflowStep, step_id=step_id).status

    def approval_task_state(self, step_id: str) -> tuple[str, str]:
        from src.core.database.repositories.uow import WorkflowUoW

        with WorkflowUoW(self.tenant_id) as uow:
            step = uow.workflows.get_by_step_id_or_raise(step_id, self._principal_id)
            return step.status, step.request_data["account"]["account_id"]

    def created_buy_ownership(self, media_buy_id: str) -> str | None:
        from src.core.database.repositories.uow import MediaBuyUoW

        with MediaBuyUoW(self.tenant_id) as uow:
            return uow.media_buys.get_by_id_or_raise(media_buy_id).account_id

    def sandbox_reference_for(self, media_buy_id: str, creative_id: str) -> str:
        from src.core.database.repositories.uow import MediaBuyUoW

        with MediaBuyUoW(self.tenant_id) as uow:
            packages = uow.media_buys.get_packages(media_buy_id)
            return packages[0].package_config["sandbox_creative_ids"][creative_id]

    def _persist_sandbox_account_condition(self, condition: str) -> None:
        from src.core.database.models import Account
        from src.core.database.repositories.uow import AccountUoW

        if condition == "revoked":
            with AccountUoW(self.tenant_id) as uow:
                uow.accounts.revoke_access(self._principal_id, "sandbox")
        else:
            self.get_one(Account, tenant_id=self.tenant_id, account_id="sandbox").status = condition
            self._commit_factory_data()

    @realize_e2e(_persist_sandbox_account_condition)
    def set_sandbox_account_condition(self, condition: str) -> None:
        """Persist the account refusal in the database used by the selected transport."""
        self._persist_sandbox_account_condition(condition)

    def remove_gam_credentials(self) -> None:
        from src.core.database.models import AdapterConfig

        config = self.get_one(AdapterConfig, tenant_id=self.tenant_id)
        config.gam_refresh_token = None
        config.gam_service_account_json = None
        self._commit_factory_data()

    def approval_session_states(self):
        from src.admin.utils import approve_media_buy_through_writer

        return self._session_states_at(approve_media_buy_through_writer)

    @contextmanager
    def _session_states_at(self, function):
        """Passively observe session ownership without replacing application code."""
        import sys

        from src.core.database.database_session import get_scoped_session

        states = []
        previous = sys.getprofile()

        def observe(frame, event, arg):
            if event == "call" and frame.f_code is function.__code__:
                states.append(get_scoped_session().registry.has())
            if previous is not None:
                previous(frame, event, arg)

        sys.setprofile(observe)
        try:
            yield states
        finally:
            sys.setprofile(previous)


class SchedulerLifecycleEnv(LocalOriginMixin, WebhookOutcomeRowsMixin, SandboxLifecycleEnv):
    """Run scheduler callbacks against real PostgreSQL and a real TLS receiver."""

    def seed_reporting_buy(self, account_id: str, media_buy_id: str, *, frequency: str = "daily") -> None:
        from src.core.database.models import MediaBuy

        self.seed_owned_buy(account_id, media_buy_id=media_buy_id, status="active")
        buy = self.get_one(MediaBuy, media_buy_id=media_buy_id)
        buy.raw_request = {
            **buy.raw_request,
            "reporting_webhook": {"url": self.webhook_url, "frequency": frequency},
        }
        self._commit_factory_data()

    def register_reporting_target(self, **overrides: Any) -> None:
        from tests.factories import PushNotificationConfigFactory

        tenant, principal = self.setup_default_data()
        fields = {
            "url": self.webhook_url,
            "authentication_type": "Bearer",
            "authentication_token": "stored-registration-secret-at-least-32-characters",
            "operation_id": "stored-operation",
            "token": "stored-correlation-token",
            **overrides,
        }
        PushNotificationConfigFactory(tenant=tenant, principal=principal, **fields)
        self._commit_factory_data()

    def register_competing_targets(self) -> None:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        self.register_reporting_target(
            created_at=now - timedelta(days=1),
            operation_id="obsolete-operation",
            authentication_token="obsolete-registration-secret-at-least-32-characters",
            token="obsolete-correlation-token",
        )
        self.register_reporting_target(created_at=now)
        self.register_reporting_target(created_at=now + timedelta(days=1), is_active=False)

    def _persist_approval_webhook(self, step_id: str) -> None:
        from src.core.database.models import WorkflowStep

        step = self.get_one(WorkflowStep, step_id=step_id)
        step.request_data = {**step.request_data, "push_notification_config": {"url": self.webhook_url}}
        self._commit_factory_data()

    @realize_e2e(_persist_approval_webhook)
    def set_approval_webhook(self, step_id: str) -> None:
        """Persist the notification target in the database used by the selected transport."""
        self._persist_approval_webhook(step_id)

    def report_sequences(self, media_buy_id: str) -> list[tuple[int, str, str]]:
        return sorted(
            (row.sequence_number, row.status, row.notification_type)
            for row in self.recorded_outcomes(media_buy_id, task_type="media_buy_delivery")
        )

    def delivery_session_states(self):
        from src.core.tools.media_buy_delivery import delivery_for_media_buy

        return self._session_states_at(delivery_for_media_buy)
