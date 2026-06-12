"""Integration tests for transport-aware mock-setup realization (#1418).

These exercise the e2e branch of the dispatch seam against a REAL Postgres DB
(the per-test ``integration_db``). E2E mode is simulated by pointing
``E2EConfig.postgres_url`` at that same DB — the env binds factories to it and
the realization writes/validates against real rows. No Docker app stack is
needed: the realization layer only touches the DB.

The in-process path of these same methods is covered by the unit harness tests
(``test_harness_delivery_poll.py``, ``test_harness_creative.py``).
"""

from __future__ import annotations

import os

import pytest

from src.core.database.models import DeliverySimulationConfig
from tests.harness._realize import E2EUnsupportedSetup
from tests.harness.transport import E2EConfig


def _e2e_config_for_integration_db() -> E2EConfig:
    """Build an E2EConfig whose postgres_url is the per-test integration DB.

    base_url is unused — these tests never dispatch over HTTP, they only assert
    on the DB rows the realization writes.
    """
    return E2EConfig(base_url="http://unused", postgres_url=os.environ["DATABASE_URL"])


@pytest.mark.requires_db
class TestDeliveryPollE2ERealization:
    """set_adapter_response persists a DeliverySimulationConfig row in e2e mode."""

    def test_set_adapter_response_writes_simulation_config_row(self, integration_db):
        from tests.harness.delivery_poll import DeliveryPollEnv

        with DeliveryPollEnv(e2e_config=_e2e_config_for_integration_db()) as env:
            env.setup_default_data()
            env.set_adapter_response("mb_e2e", impressions=7000, spend=350.0)

            row = env.get_one(DeliverySimulationConfig, tenant_id=env._tenant_id, media_buy_id="mb_e2e")
            assert row is not None
            payload = row.response_payload
            assert payload["media_buy_id"] == "mb_e2e"
            assert payload["totals"]["impressions"] == 7000.0
            assert payload["totals"]["spend"] == 350.0

            # The in-process dict must NOT be populated in e2e mode.
            assert env._adapter_responses == {}

    def test_set_adapter_response_multipackage_totals(self, integration_db):
        from tests.harness.delivery_poll import DeliveryPollEnv

        with DeliveryPollEnv(e2e_config=_e2e_config_for_integration_db()) as env:
            env.setup_default_data()
            env.set_adapter_response(
                "mb_multi",
                packages=[
                    {"package_id": "pkg_a", "impressions": 1000, "spend": 50.0},
                    {"package_id": "pkg_b", "impressions": 2000, "spend": 100.0},
                ],
            )

            row = env.get_one(DeliverySimulationConfig, tenant_id=env._tenant_id, media_buy_id="mb_multi")
            assert row is not None
            payload = row.response_payload
            assert payload["totals"]["impressions"] == 3000.0
            assert payload["totals"]["spend"] == 150.0
            assert len(payload["by_package"]) == 2

    def test_set_adapter_response_upserts_same_media_buy(self, integration_db):
        """Calling twice for the same media buy updates (does not duplicate)."""
        from tests.harness.delivery_poll import DeliveryPollEnv

        with DeliveryPollEnv(e2e_config=_e2e_config_for_integration_db()) as env:
            env.setup_default_data()
            env.set_adapter_response("mb_upsert", impressions=100, spend=10.0)
            env.set_adapter_response("mb_upsert", impressions=999, spend=99.0)

            rows = env.query(DeliverySimulationConfig, tenant_id=env._tenant_id, media_buy_id="mb_upsert")
            assert len(rows) == 1
            assert rows[0].response_payload["totals"]["impressions"] == 999.0

    def test_set_adapter_error_is_unrealizable(self, integration_db):
        from tests.harness.delivery_poll import DeliveryPollEnv

        with DeliveryPollEnv(e2e_config=_e2e_config_for_integration_db()) as env:
            env.setup_default_data()
            with pytest.raises(E2EUnsupportedSetup) as exc_info:
                env.set_adapter_error(RuntimeError("adapter down"))
            assert exc_info.value.method_name == "set_adapter_error"
            assert "fault-injection" in str(exc_info.value)


@pytest.mark.requires_db
class TestDeliveryPollDiscoverySeeding:
    """E2E mode seeds tenant + principal so the live server can authenticate."""

    def test_seed_e2e_identity_creates_tenant_and_principal(self, integration_db):
        from src.core.database.models import Principal, Tenant
        from tests.harness.delivery_poll import DeliveryPollEnv

        # Do NOT call setup_default_data — discovery scenarios never do. The env
        # must seed identity itself on entry in e2e mode.
        with DeliveryPollEnv(e2e_config=_e2e_config_for_integration_db()) as env:
            tenant = env.get_one(Tenant, tenant_id=env._tenant_id)
            principal = env.get_one(Principal, tenant_id=env._tenant_id, principal_id=env._principal_id)
            assert tenant is not None
            assert principal is not None

    def test_seeding_is_idempotent_with_setup_default_data(self, integration_db):
        """setup_default_data after auto-seed must not duplicate rows."""
        from src.core.database.models import Principal, Tenant
        from tests.harness.delivery_poll import DeliveryPollEnv

        with DeliveryPollEnv(e2e_config=_e2e_config_for_integration_db()) as env:
            env.setup_default_data()  # explicit, on top of __enter__ auto-seed
            tenants = env.query(Tenant, tenant_id=env._tenant_id)
            principals = env.query(Principal, tenant_id=env._tenant_id, principal_id=env._principal_id)
            assert len(tenants) == 1
            assert len(principals) == 1


@pytest.mark.requires_db
class TestCreativeFormatsE2EValidation:
    """set_registry_formats validates against the reference catalog in e2e mode."""

    def test_subset_of_reference_is_noop(self, integration_db):
        from src.core.format_cache import load_reference_formats
        from tests.harness.creative_formats import CreativeFormatsEnv

        reference = list(load_reference_formats())
        with CreativeFormatsEnv(e2e_config=_e2e_config_for_integration_db()) as env:
            # A subset of the real catalog -> realizable, no exception.
            env.set_registry_formats(reference[:3])

    def test_empty_catalog_is_unrealizable(self, integration_db):
        from tests.harness.creative_formats import CreativeFormatsEnv

        with CreativeFormatsEnv(e2e_config=_e2e_config_for_integration_db()) as env:
            with pytest.raises(E2EUnsupportedSetup) as exc_info:
                env.set_registry_formats([])
            assert "empty catalog" in str(exc_info.value)

    def test_unknown_format_is_unrealizable_and_named(self, integration_db):
        from src.core.format_cache import load_reference_formats
        from src.core.schemas import Format
        from tests.harness.creative_formats import CreativeFormatsEnv

        # Build a Format with an id not in the reference catalog.
        bogus = load_reference_formats()[0].model_copy(deep=True)
        bogus.format_id.id = "totally_made_up_format_xyz"
        assert isinstance(bogus, Format)

        with CreativeFormatsEnv(e2e_config=_e2e_config_for_integration_db()) as env:
            with pytest.raises(E2EUnsupportedSetup) as exc_info:
                env.set_registry_formats([bogus])
            assert "totally_made_up_format_xyz" in str(exc_info.value)


@pytest.mark.requires_db
class TestAccountSyncQuietFailureFix:
    """set_billing_policy / set_approval_mode raise instead of silently skipping."""

    def test_set_billing_policy_before_tenant_raises(self, integration_db):
        from tests.harness.account_sync import AccountSyncEnv

        with AccountSyncEnv() as env:
            # No tenant row yet (setup_default_data not called).
            with pytest.raises(RuntimeError, match="requires the tenant row"):
                env.set_billing_policy(["operator"])

    def test_set_approval_mode_before_tenant_raises(self, integration_db):
        from tests.harness.account_sync import AccountSyncEnv

        with AccountSyncEnv() as env:
            with pytest.raises(RuntimeError, match="requires the tenant row"):
                env.set_approval_mode("manual")

    def test_set_billing_policy_after_tenant_writes_db(self, integration_db):
        from src.core.database.models import Tenant
        from tests.harness.account_sync import AccountSyncEnv

        with AccountSyncEnv() as env:
            env.setup_default_data()
            env.set_billing_policy(["operator", "buyer"])

            tenant = env.get_one(Tenant, tenant_id=env._tenant_id)
            assert tenant.supported_billing == ["operator", "buyer"]

    def test_constructor_billing_folds_into_db_via_setup(self, integration_db):
        """Constructor-passed supported_billing reaches the DB row, not just memory."""
        from src.core.database.models import Tenant
        from tests.harness.account_sync import AccountSyncEnv

        with AccountSyncEnv(supported_billing=["operator"], account_approval_mode="manual") as env:
            env.setup_default_data()

            tenant = env.get_one(Tenant, tenant_id=env._tenant_id)
            assert tenant.supported_billing == ["operator"]
            assert tenant.account_approval_mode == "manual"
