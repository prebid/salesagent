"""Integration tests pinning salesagent-689e's Core Invariant.

Core Invariant (salesagent-689e): the mock-adapter DB-config
``AdapterConfig.config_json["test_behavior"]`` channel is the ONE
per-tenant fault-injection seam shared by in-process and e2e transports.
Every new fault-injection capability (adapter "unavailable", targeting
capability-shape override) must extend that seam through a SINGLE read
helper in ``src/core/helpers/adapter_helpers.py`` -- never a raw
``get_db_session()`` inside ``src/core/tools/capabilities.py``'s ``_impl``
(that file's ``IMPL_SESSION_ALLOWLIST`` entry in
``test_architecture_repository_pattern.py`` is EMPTY, so any such call
fails ``make quality`` with no allowlist-growth escape), and it must
never leak onto non-mock adapter types or the real media-buy path.

These tests exercise the REAL adapter-resolution path end to end (real
Postgres via ``IntegrationEnv``, real ``AdapterConfigRepository`` read,
real ``get_adapter_class_for_tenant`` -- nothing patched) so they fail
today because the production code has no read of
``test_behavior["unavailable"]`` / ``test_behavior["targeting_capabilities"]``
at all yet.

Covers: salesagent-689e (Core Invariant + Implementation Plan steps 1-3).
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.enums.channels import MediaChannel

from tests.factories.core import set_adapter_test_behavior
from tests.harness._base import IntegrationEnv


@pytest.mark.requires_db
class TestAdapterUnavailableFaultInjection:
    """test_behavior['unavailable']=True must make adapter-class resolution raise."""

    def test_unavailable_flag_degrades_capabilities_to_display_only(self, integration_db):
        """A per-tenant 'unavailable' flag on the mock adapter's test_behavior
        must make get_adapter_class_for_tenant() raise, so
        _get_adcp_capabilities_impl's existing try/except degrades
        primary_channels to the [display]-only fallback -- NOT the mock
        adapter's real default_channels (["display", "olv", "streaming_audio",
        "social"]), which is what the real (un-faulted) mock always reports.
        """
        with IntegrationEnv(tenant_id="t_fault_unavail", principal_id="p_fault_unavail") as env:
            tenant, _principal = env.setup_default_data()
            set_adapter_test_behavior(env, tenant.tenant_id, unavailable=True)

            from src.core.tools.capabilities import _get_adcp_capabilities_impl

            response = _get_adcp_capabilities_impl(None, env.identity)

        assert response.media_buy is not None
        channels = response.media_buy.portfolio.primary_channels
        assert channels == [MediaChannel.display], (
            "expected the [display]-only degraded fallback once "
            "test_behavior['unavailable'] makes adapter resolution raise, "
            f"got {channels!r} (the real mock adapter's full default_channels "
            "leaking through means the 'unavailable' flag was never read)"
        )

    def test_unavailable_flag_does_not_affect_a_non_mock_tenant(self, integration_db):
        """The 'unavailable' fault must be gated on adapter_type == 'mock' --
        a tenant on a different (non-mock) adapter_type is unaffected even if
        a stray test_behavior row exists, per the Core Invariant's
        'never leak onto non-mock adapter types' clause.
        """
        with IntegrationEnv(tenant_id="t_fault_gam", principal_id="p_fault_gam") as env:
            tenant, _principal = env.setup_default_data()
            from tests.factories import AdapterConfigFactory

            AdapterConfigFactory(
                tenant=tenant,
                adapter_type="google_ad_manager",
                config_json={"test_behavior": {"unavailable": True}},
            )
            env._commit_factory_data()

            from src.core.helpers.adapter_helpers import get_adapter_class_for_tenant

            # Must resolve the GAM adapter class without raising -- the
            # 'unavailable' flag is scoped to mock-adapter tenants only.
            adapter_class = get_adapter_class_for_tenant(tenant)

        assert adapter_class.__name__ == "GoogleAdManager", (
            f"expected GoogleAdManager to resolve normally, got {adapter_class!r} -- "
            "the mock-only 'unavailable' fault-injection flag must not raise for "
            "non-mock adapter types"
        )


@pytest.mark.requires_db
class TestTargetingCapabilitiesOverrideFaultInjection:
    """test_behavior['targeting_capabilities'] must override the adapter's declared shape."""

    def test_targeting_override_replaces_adapter_declared_shape(self, integration_db):
        """MockAdServerAdapter.get_targeting_capabilities() is a hardcoded
        all-True staticmethod (including nielsen_dma / eurostat_nuts2 / uk_itl1
        / uk_itl2 metro dimensions). A per-tenant
        test_behavior['targeting_capabilities'] override that declares only
        geo_countries/geo_regions (no metro dims) must replace that shape in
        the capabilities response -- so geo_metros must come back None,
        not populated from the adapter's real hardcoded declaration.
        """
        with IntegrationEnv(tenant_id="t_fault_targeting", principal_id="p_fault_targeting") as env:
            tenant, _principal = env.setup_default_data()
            set_adapter_test_behavior(
                env,
                tenant.tenant_id,
                targeting_capabilities={"geo_countries": True, "geo_regions": True},
            )

            from src.core.tools.capabilities import _get_adcp_capabilities_impl

            response = _get_adcp_capabilities_impl(None, env.identity)

        assert response.media_buy is not None
        targeting = response.media_buy.execution.targeting
        assert targeting.geo_metros is None, (
            "expected geo_metros=None because the per-tenant targeting_capabilities "
            "override declares no metro dimensions -- a non-None geo_metros means "
            "the real (un-overridden) mock adapter's hardcoded all-True "
            "TargetingCapabilities leaked through instead of the DB override"
        )
