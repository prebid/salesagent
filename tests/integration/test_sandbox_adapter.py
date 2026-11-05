"""Internal adapter/repository contracts have no wire; lifecycle coverage lives in BDD.

AdCP 3.1.1 sandbox.mdx requires normal validation and simulated execution.
Vendor-call suppression is ungraded by the inspected upstream storyboards.
"""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest

from src.adapters.base import AdapterCreateRequest
from src.adapters.google_ad_manager import GoogleAdManager
from src.adapters.kevel import Kevel
from src.adapters.mock_ad_server import MockAdServer
from src.adapters.sandbox import SandboxAdServerAdapter
from src.adapters.triton_digital import TritonDigital
from src.core.exceptions import AdCPCapabilityNotSupportedError, AdCPMediaBuyNotFoundError
from src.core.helpers.adapter_helpers import get_adapter
from src.core.resolved_identity import identity_of
from src.core.schemas import MediaPackage, ReportingPeriod
from tests.factories import PrincipalFactory, TargetingFactory
from tests.harness.sandbox_adapter import SandboxAdapterEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def test_sandbox_factory_works_without_live_credentials(integration_db):
    with SandboxAdapterEnv() as env:
        tenant, principal = env.seed_configuration()
        configuration_reads = env.observe_configuration_reads()
        adapter = get_adapter(identity_of(tenant, principal, "sandbox"))
        assert isinstance(adapter, SandboxAdServerAdapter)
        assert adapter.configured_adapter_type == "google_ad_manager"
        assert adapter.manual_approval_required is True
        assert len(configuration_reads) == 1
        assert all(
            secret_column not in configuration_reads[0]
            for secret_column in ("gam_refresh_token", "gam_service_account_json", "kevel_api_key", "triton_api_key")
        )
        assert env.mock["gam_client"].call_count == 0
        assert env.mock["gam_oauth"].call_count == 0


@pytest.mark.parametrize(
    "adapter_type,approval,expected",
    [
        ("google_ad_manager", True, True),
        ("google_ad_manager", False, False),
        ("mock", True, True),
        ("mock", False, False),
        ("kevel", True, True),
        ("kevel", False, False),
        ("triton", True, False),
    ],
)
def test_sandbox_preserves_configured_approval(integration_db, adapter_type, approval, expected):
    with SandboxAdapterEnv() as env:
        tenant, principal = env.seed_configuration(adapter_type, approval)
        adapter = get_adapter(identity_of(tenant, principal, "sandbox"))
        assert isinstance(adapter, SandboxAdServerAdapter)
        assert adapter.manual_approval_required is expected
        assert adapter.configured_adapter_type == adapter_type


@pytest.mark.parametrize(
    "adapter_type,adapter_class",
    [
        ("google_ad_manager", GoogleAdManager),
        ("mock", MockAdServer),
        ("broadstreet", MockAdServer),
        ("gam", MockAdServer),
        ("creative_engine", MockAdServer),
        ("unregistered", MockAdServer),
    ],
)
@pytest.mark.parametrize("approval", [True, False])
def test_live_control_keeps_configured_adapter_and_policy(integration_db, adapter_type, adapter_class, approval):
    with SandboxAdapterEnv() as env:
        tenant, principal = env.seed_configuration(adapter_type, approval, credentials=True)
        live = get_adapter(identity_of(tenant, principal, "live"))
        sandbox = get_adapter(identity_of(tenant, principal, "sandbox"))
        assert isinstance(live, adapter_class)
        assert live.manual_approval_required is sandbox.manual_approval_required
        assert env.mock["gam_client"].call_count == (1 if adapter_type == "google_ad_manager" else 0)
        assert env.mock["gam_oauth"].call_count == (1 if adapter_type == "google_ad_manager" else 0)


def test_missing_configuration_retains_tenant_fallback_and_no_approval_default(integration_db):
    with SandboxAdapterEnv() as env:
        tenant, principal = env.seed_configuration(tenant_adapter="mock", configured=False)
        sandbox = get_adapter(identity_of(tenant, principal, "sandbox"))
        live = get_adapter(identity_of(tenant, principal, "live"))
        assert sandbox.configured_adapter_type == "mock"
        assert sandbox.manual_approval_required is live.manual_approval_required is False


@pytest.mark.parametrize(
    "adapter_type,pricing,media_type,expected",
    [
        ("google_ad_manager", "cpm", None, []),
        ("google_ad_manager", "cpm", "display", []),
        ("google_ad_manager", "cpm", "audio", None),
        ("google_ad_manager", "cpcv", None, [("UNSUPPORTED_FEATURE", "packages[0].pricing_option_id")]),
        ("mock", "cpm", None, [("VALIDATION_ERROR", "packages[0].impressions"), ("VALIDATION_ERROR", "budget")]),
        ("broadstreet", "cpm", None, [("VALIDATION_ERROR", "packages[0].impressions"), ("VALIDATION_ERROR", "budget")]),
        ("gam", "cpm", None, [("VALIDATION_ERROR", "packages[0].impressions"), ("VALIDATION_ERROR", "budget")]),
        (
            "creative_engine",
            "cpm",
            None,
            [("VALIDATION_ERROR", "packages[0].impressions"), ("VALIDATION_ERROR", "budget")],
        ),
        (
            "unregistered",
            "cpm",
            None,
            [("VALIDATION_ERROR", "packages[0].impressions"), ("VALIDATION_ERROR", "budget")],
        ),
    ],
)
def test_validation_uses_configured_rules_without_vendor_instance(
    integration_db, adapter_type, pricing, media_type, expected
):
    with SandboxAdapterEnv() as env:
        tenant, principal = env.seed_configuration(adapter_type, credentials=True)
        start = datetime.now(UTC) + timedelta(days=1)
        package = MediaPackage(
            package_id="package",
            name="Package",
            delivery_type="guaranteed",
            cpm=10,
            impressions=2_000_000,
            budget=2_000_000,
            format_ids=[],
            targeting_overlay=TargetingFactory(media_type_any_of=[media_type]) if media_type else None,
        )
        for account in ("sandbox", "live"):
            adapter = get_adapter(identity_of(tenant, principal, account))
            refusal = pytest.raises(AdCPCapabilityNotSupportedError) if expected is None else nullcontext()
            with refusal:
                problems = adapter.validate_media_buy_request(
                    AdapterCreateRequest(total_budget=2_000_000),
                    [package],
                    start,
                    start + timedelta(days=7),
                    {"package": {"pricing_model": pricing}},
                )
                if expected is not None:
                    assert [(str(p.code), p.field) for p in problems] == expected
            if account == "sandbox":
                assert env.mock["gam_client"].call_count == 0
                assert env.mock["gam_oauth"].call_count == 0


@pytest.mark.parametrize(
    "adapter_type,targeting",
    [
        ("google_ad_manager", {"device_platform": ["ios"]}),
        ("google_ad_manager", {"device_type_any_of": ["mobile"]}),
        ("google_ad_manager", {"os_any_of": ["ios"]}),
        ("google_ad_manager", {"browser_any_of": ["chrome"]}),
        ("google_ad_manager", {"content_cat_any_of": ["IAB1"]}),
        ("google_ad_manager", {"keywords_any_of": ["sports"]}),
        ("google_ad_manager", {"audiences_any_of": ["segment"]}),
        ("google_ad_manager", {"signals": ["signal"]}),
        ("google_ad_manager", {"media_type_any_of": ["display", "video"]}),
        ("google_ad_manager", {"geo_metros": [{"system": "uk_itl1", "values": ["TLG"]}]}),
        ("google_ad_manager", {"geo_metros_exclude": [{"system": "eurostat_nuts2", "values": ["DE1"]}]}),
        ("kevel", {"media_type_any_of": ["video"]}),
        ("kevel", {"device_type_any_of": ["ctv"]}),
        ("kevel", {"audiences_any_of": ["segment"]}),
        ("kevel", {"frequency_cap": {"suppress_minutes": 60}}),
        ("triton", {"media_type_any_of": ["display"]}),
        ("triton", {"device_type_any_of": ["tablet"]}),
        ("triton", {"browser_any_of": ["chrome"]}),
        ("triton", {"content_cat_any_of": ["IAB1"]}),
    ],
)
def test_sandbox_refuses_existing_local_execution_constraints(integration_db, adapter_type, targeting):
    with SandboxAdapterEnv() as env:
        tenant, principal = env.seed_configuration(adapter_type, credentials=True)
        sandbox = get_adapter(identity_of(tenant, principal, "sandbox"))
        package = MediaPackage(
            package_id="package",
            name="Package",
            delivery_type="guaranteed",
            cpm=10,
            impressions=1000,
            format_ids=[],
            targeting_overlay=TargetingFactory(**targeting),
        )
        start = datetime.now(UTC) + timedelta(days=1)
        with pytest.raises(AdCPCapabilityNotSupportedError):
            sandbox.validate_media_buy_request(
                AdapterCreateRequest(total_budget=10), [package], start, start + timedelta(days=7)
            )
        assert env.mock["gam_client"].call_count == 0
        assert env.mock["gam_oauth"].call_count == 0
        # Independently exercise the live execution guard, not only its prevalidator.
        if adapter_type == "google_ad_manager":
            live = get_adapter(identity_of(tenant, principal, "live"))
            with pytest.raises(AdCPCapabilityNotSupportedError):
                live.targeting_manager.build_targeting(package.targeting_overlay)
        else:
            adapter_class = Kevel if adapter_type == "kevel" else TritonDigital
            identity = PrincipalFactory.make_identity(platform_mappings={adapter_type: {"advertiser_id": "123"}})
            live = adapter_class(
                {"network_id": "123", "api_key": "test", "auth_token": "test"}, identity.principal, tenant_id=tenant
            )
            assert live._validate_targeting(package.targeting_overlay)
            with pytest.raises(AdCPCapabilityNotSupportedError):
                live.validate_media_buy_request(
                    AdapterCreateRequest(total_budget=10), [package], start, start + timedelta(days=7)
                )


@pytest.mark.parametrize("scope,refused", [("package", False), ("media_buy", True)])
def test_kevel_prevalidation_preserves_enabled_feature_flags(integration_db, scope, refused):
    identity = PrincipalFactory.make_identity(platform_mappings={"kevel": {"advertiser_id": "123"}})
    live = Kevel(
        {"network_id": "123", "api_key": "test", "userdb_enabled": True, "frequency_capping_enabled": True},
        identity.principal,
        tenant_id=identity.tenant_id,
    )
    package = MediaPackage(
        package_id="package",
        name="Package",
        delivery_type="guaranteed",
        cpm=10,
        impressions=1000,
        format_ids=[],
        targeting_overlay=TargetingFactory(
            audiences_any_of=["segment"], frequency_cap={"suppress_minutes": 60, "scope": scope}
        ),
    )
    start = datetime.now(UTC) + timedelta(days=1)
    with pytest.raises(AdCPCapabilityNotSupportedError) if refused else nullcontext():
        problems = live.validate_media_buy_request(
            AdapterCreateRequest(total_budget=10), [package], start, start + timedelta(days=7)
        )
        assert problems == []


def test_synthetic_ids_do_not_alias_purchase_order_text(integration_db):
    with SandboxAdapterEnv() as env:
        tenant, principal = env.seed_configuration()
        start = datetime.now(UTC) + timedelta(days=1)
        request = AdapterCreateRequest(total_budget=100, po_number="same [REJECT]")
        first = get_adapter(identity_of(tenant, principal, "sandbox")).create_media_buy(
            request, [], start, start + timedelta(days=7)
        )
        second = get_adapter(identity_of(tenant, principal, "sibling")).create_media_buy(
            request, [], start, start + timedelta(days=7)
        )
        assert first.media_buy_id.startswith("sandbox_")
        assert first.media_buy_id != second.media_buy_id


@pytest.mark.parametrize("seeded", [False, True])
@pytest.mark.parametrize("testing", [False, True])
def test_fresh_adapter_reads_only_owning_account_state(integration_db, monkeypatch, seeded, testing):
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings().testing, "adcp_testing", testing)
    with SandboxAdapterEnv() as env:
        tenant, principal = env.seed_configuration()
        env.stored_buy(status="active")
        if seeded:
            env.seed_delivery()
        date_range = ReportingPeriod(start="2026-10-01T00:00:00Z", end="2026-10-10T00:00:00Z")
        now = datetime.now(UTC)
        owner = get_adapter(identity_of(tenant, principal, "sandbox"))
        delivery = owner.get_media_buy_delivery("stored", date_range, now)
        assert (delivery.totals.impressions, delivery.totals.spend) == ((4321, 43.21) if seeded else (0, 0))
        assert [package.package_id for package in delivery.by_package] == ["package"]
        assert owner.check_media_buy_status("stored", now).status == "active"
        snapshot = owner.get_packages_snapshot([("stored", "package", None)])["stored"]["package"]
        assert (snapshot.impressions, snapshot.spend) == ((4321, 43.21) if seeded else (0, 0))
        sibling = get_adapter(identity_of(tenant, principal, "sibling"))
        with pytest.raises(AdCPMediaBuyNotFoundError):
            sibling.get_media_buy_delivery("stored", date_range, now)
        assert sibling.get_packages_snapshot([("stored", "package", None)]) == {"stored": {"package": None}}
        assert owner.get_packages_snapshot([("stored", "missing-package", None)]) == {
            "stored": {"missing-package": None}
        }


def test_missing_buy_is_refused_even_with_seeded_delivery(integration_db):
    with SandboxAdapterEnv() as env:
        tenant, principal = env.seed_configuration()
        env.seed_delivery("missing")
        adapter = get_adapter(identity_of(tenant, principal, "sandbox"))
        period = ReportingPeriod(start="2026-10-01T00:00:00Z", end="2026-10-10T00:00:00Z")
        with pytest.raises(AdCPMediaBuyNotFoundError):
            adapter.get_media_buy_delivery("missing", period, datetime.now(UTC))


def test_repository_reads_preserve_callers_pending_domain_changes(integration_db):
    with SandboxAdapterEnv() as env:
        tenant, principal = env.seed_configuration()
        env.stored_buy(status="active")
        assert env.persist_through_adapter_reads(identity_of(tenant, principal, "sandbox")) == "Survived adapter reads"


def test_sandbox_execution_returns_package_and_creative_results_without_domain_writes(integration_db):
    with SandboxAdapterEnv() as env:
        tenant, principal = env.seed_configuration()
        env.stored_buy(status="active")
        adapter = get_adapter(identity_of(tenant, principal, "sandbox"))
        now = datetime.now(UTC)
        package = MediaPackage(
            package_id="package", name="P", delivery_type="guaranteed", cpm=10, impressions=1000, format_ids=[]
        )
        created = adapter.create_media_buy(
            AdapterCreateRequest(total_budget=100), [package], now, now + timedelta(days=1)
        )
        assert [package.package_id for package in created.packages] == ["package"]
        assert created.platform_line_item_ids["package"].startswith("sandbox_line_")
        assets = adapter.add_creative_assets("stored", [{"id": "creative", "name": "[REJECT]"}], now)
        assert [(asset.asset_id, asset.status) for asset in assets] == [("creative", "approved")]
        assert assets[0].creative_id.startswith("sandbox_creative_")
        assert adapter.associate_creatives(["line"], [assets[0].creative_id]) == [
            {"line_item_id": "line", "creative_id": assets[0].creative_id, "status": "success"}
        ]
        result = adapter.update_media_buy("stored", "update_package_budget", "package", 999, now)
        assert (result.media_buy_id, result.affected_packages) == ("stored", [])
        assert env.package_budget("stored", "package") == 100
        assert env.mock["gam_client"].call_count == 0
        assert env.mock["gam_oauth"].call_count == 0


@pytest.mark.parametrize(
    "tenant_scope,principal_scope,account_scope",
    [
        ("foreign-tenant", "test_principal", "sandbox"),
        ("test_tenant", "foreign-principal", "sandbox"),
        ("test_tenant", "test_principal", "sibling"),
    ],
)
def test_scoped_repository_lookup_requires_every_ownership_dimension(
    integration_db,
    tenant_scope,
    principal_scope,
    account_scope,
):
    from src.core.database.repositories.uow import MediaBuyUoW

    with SandboxAdapterEnv() as env:
        env.seed_configuration()
        env.stored_buy()
        with MediaBuyUoW(tenant_scope) as uow:
            assert uow.media_buys.get_by_id_for_account("stored", principal_scope, account_scope) is None


def test_configuration_row_precedes_gam_tenant_fallback(integration_db):
    with SandboxAdapterEnv() as env:
        tenant, principal = env.seed_configuration("mock", False, tenant_adapter="google_ad_manager")
        sandbox = get_adapter(identity_of(tenant, principal, "sandbox"))
        live = get_adapter(identity_of(tenant, principal, "live"))
        assert sandbox.configured_adapter_type == "mock"
        assert isinstance(live, MockAdServer)
        assert sandbox.manual_approval_required is live.manual_approval_required is False
