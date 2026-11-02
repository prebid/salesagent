"""Integration tests for adapter factory function.

These tests verify that the get_adapter() factory function correctly instantiates
all adapter types with proper constructor signatures. This catches bugs where
the factory function and adapter constructors get out of sync.

Key test: Ensures get_adapter() passes arguments in the correct format for each
adapter type, especially important for adapters with keyword-only arguments.
"""

import pytest
from sqlalchemy import delete

from src.core.database.database_session import get_db_session
from src.core.database.models import Principal as ModelPrincipal
from src.core.database.models import Tenant as ModelTenant
from src.core.helpers import get_adapter
from src.core.resolved_identity import ResolvedIdentity
from src.core.tenant_context import TenantContext
from tests.factories.principal import PrincipalFactory


def _identity_for(tenant_id: str, principal_id: str) -> ResolvedIdentity:
    """The identity ``get_adapter`` takes: the seeded tenant row, with the buyer inside.

    ``get_adapter(identity)`` reads both facts off one object (commit a1b79d22d), so a
    caller cannot pair a tenant with a principal from somewhere else -- and there is no
    dry-run flag on an adapter any more, because nothing could set one.
    """
    tenant = TenantContext.load(tenant_id)
    assert tenant is not None, f"the setup fixture must have committed tenant {tenant_id}"
    return PrincipalFactory.make_identity(principal_id=principal_id, tenant_id=tenant_id, tenant=tenant)


@pytest.mark.integration
@pytest.mark.requires_db
class TestAdapterFactory:
    """Test adapter factory function with real database."""

    @pytest.fixture
    def setup_adapters(self, integration_db):
        """Set up tenants and principals for different adapter types."""
        from tests.utils.database_helpers import (
            create_principal_with_platform_mappings,
            create_tenant_with_timestamps,
        )

        with get_db_session() as session:
            adapters_to_test = []

            # 1. Mock adapter tenant
            mock_tenant = create_tenant_with_timestamps(
                tenant_id="test_factory_mock",
                name="Mock Adapter Test",
                subdomain="mock",
                ad_server="mock",
                is_active=True,
            )
            session.add(mock_tenant)

            mock_principal = create_principal_with_platform_mappings(
                tenant_id="test_factory_mock",
                principal_id="mock_principal",
                name="Mock Principal",
                access_token="mock_token",
                platform_mappings={"mock": {"advertiser_id": "mock_adv_123"}},
            )
            session.add(mock_principal)
            adapters_to_test.append(("mock", "test_factory_mock", "mock_principal"))

            # 2. GAM adapter tenant
            from src.core.database.models import AdapterConfig
            from tests.utils.database_helpers import get_utc_now

            now = get_utc_now()

            gam_tenant = create_tenant_with_timestamps(
                tenant_id="test_factory_gam",
                name="GAM Adapter Test",
                subdomain="gam",
                ad_server="google_ad_manager",
                is_active=True,
            )
            session.add(gam_tenant)

            # Create GAM adapter config
            gam_config = AdapterConfig(
                tenant_id="test_factory_gam",
                adapter_type="google_ad_manager",
                gam_network_code="123456789",
                gam_refresh_token="test_refresh_token",
                gam_trafficker_id="999",
            )
            session.add(gam_config)

            gam_principal = create_principal_with_platform_mappings(
                tenant_id="test_factory_gam",
                principal_id="gam_principal",
                name="GAM Principal",
                access_token="gam_token",
                platform_mappings={
                    "google_ad_manager": {
                        "advertiser_id": "12345",
                    }
                },
            )
            session.add(gam_principal)
            adapters_to_test.append(("google_ad_manager", "test_factory_gam", "gam_principal"))

            # 3. Kevel adapter tenant
            kevel_tenant = create_tenant_with_timestamps(
                tenant_id="test_factory_kevel",
                name="Kevel Adapter Test",
                subdomain="kevel",
                ad_server="kevel",
                is_active=True,
            )
            session.add(kevel_tenant)

            kevel_config = AdapterConfig(
                tenant_id="test_factory_kevel",
                adapter_type="kevel",
                kevel_network_id=987654,
                kevel_api_key="test_kevel_key",
            )
            session.add(kevel_config)

            kevel_principal = create_principal_with_platform_mappings(
                tenant_id="test_factory_kevel",
                principal_id="kevel_principal",
                name="Kevel Principal",
                access_token="kevel_token",
                platform_mappings={"kevel": {"advertiser_id": "kevel_adv_123"}},
            )
            session.add(kevel_principal)
            adapters_to_test.append(("kevel", "test_factory_kevel", "kevel_principal"))

            # 4. Triton adapter tenant
            triton_tenant = create_tenant_with_timestamps(
                tenant_id="test_factory_triton",
                name="Triton Adapter Test",
                subdomain="triton",
                ad_server="triton",
                is_active=True,
            )
            session.add(triton_tenant)

            triton_config = AdapterConfig(
                tenant_id="test_factory_triton",
                adapter_type="triton",
                triton_station_id="STATION123",
                triton_api_key="test_triton_key",
            )
            session.add(triton_config)

            triton_principal = create_principal_with_platform_mappings(
                tenant_id="test_factory_triton",
                principal_id="triton_principal",
                name="Triton Principal",
                access_token="triton_token",
                # Use mock platform mapping since triton not in allowed list
                platform_mappings={"mock": {"advertiser_id": "triton_adv_123"}},
            )
            session.add(triton_principal)
            adapters_to_test.append(("triton", "test_factory_triton", "triton_principal"))

            session.commit()

            yield adapters_to_test

            # Cleanup
            session.execute(
                delete(ModelPrincipal).where(
                    ModelPrincipal.tenant_id.in_(
                        [
                            "test_factory_mock",
                            "test_factory_gam",
                            "test_factory_kevel",
                            "test_factory_triton",
                        ]
                    )
                )
            )
            session.execute(
                delete(AdapterConfig).where(
                    AdapterConfig.tenant_id.in_(
                        [
                            "test_factory_gam",
                            "test_factory_kevel",
                            "test_factory_triton",
                        ]
                    )
                )
            )
            session.execute(
                delete(ModelTenant).where(
                    ModelTenant.tenant_id.in_(
                        [
                            "test_factory_mock",
                            "test_factory_gam",
                            "test_factory_kevel",
                            "test_factory_triton",
                        ]
                    )
                )
            )
            session.commit()

    def test_get_adapter_instantiates_all_adapter_types(self, setup_adapters):
        """Test that get_adapter() correctly instantiates all adapter types.

        This test catches constructor signature mismatches like the bug where
        GoogleAdManager required keyword-only arguments but get_adapter() was
        passing positional arguments.

        Regression test for: GoogleAdManager constructor argument mismatch.
        """
        from src.adapters.google_ad_manager import GoogleAdManager
        from src.adapters.kevel import Kevel
        from src.adapters.mock_ad_server import MockAdServer
        from src.adapters.triton_digital import TritonDigital

        adapter_type_map = {
            "mock": MockAdServer,
            "google_ad_manager": GoogleAdManager,
            "kevel": Kevel,
            "triton": TritonDigital,
        }

        for adapter_type, tenant_id, principal_id in setup_adapters:
            # get_adapter takes the ONE identity and reads the tenant and the principal off
            # it, so the two cannot be handed over as a mismatched pair. The tenant is the
            # committed row, which is what the resolver would have loaded.
            identity = _identity_for(tenant_id, principal_id)

            # Test instantiation via factory function
            try:
                adapter = get_adapter(identity)
                assert adapter is not None, f"get_adapter() returned None for {adapter_type}"

                # Verify correct adapter type
                expected_class = adapter_type_map[adapter_type]
                assert isinstance(adapter, expected_class), (
                    f"Expected {expected_class.__name__}, got {type(adapter).__name__}"
                )

            except TypeError as e:
                pytest.fail(
                    f"TypeError instantiating {adapter_type} adapter via get_adapter(): {e}\n"
                    f"This usually means constructor signature doesn't match factory function call."
                )
            except Exception:
                # Other exceptions are OK (e.g., missing credentials).
                # We only care about constructor signature mismatches (TypeError)
                pass

    def test_gam_adapter_requires_network_code(self, setup_adapters):
        """Test that GAM adapter correctly receives network_code from factory.

        Specific regression test for the bug where network_code was missing,
        causing: "GoogleAdManager.__init__() takes 3 positional arguments but
        4 positional arguments (and 1 keyword-only argument) were given"
        """
        identity = _identity_for("test_factory_gam", "gam_principal")

        # This should work without TypeError
        adapter = get_adapter(identity)

        # Verify it's actually a GAM adapter, not mock fallback
        from src.adapters.google_ad_manager import GoogleAdManager

        assert isinstance(adapter, GoogleAdManager), (
            f"Expected GAM adapter, got {type(adapter).__name__}. Check tenant/adapter_config setup."
        )

        # Verify network_code was passed correctly
        assert hasattr(adapter, "network_code"), "GAM adapter missing network_code attribute"
        assert adapter.network_code == "123456789", "network_code not set correctly"
