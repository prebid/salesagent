"""Integration tests for adapter_helpers.py's Optional-tenant ContextVar fallback.

Each of get_adapter_class_for_tenant(), get_targeting_capabilities_override(), and
resolve_manual_approval_signal() documents "Falls back to ContextVar if not
provided" -- the same contract get_adapter() and resolve_tenant_adapter_type()
already honor via set_current_tenant()/get_current_tenant(). The dict shape
below (`{"tenant_id": ..., "ad_server": ...}`) is not a synthetic stand-in --
it is copied verbatim from src/admin/blueprints/operations.py:233-238, the one
real caller that sets the ContextVar this way and then (line 247) calls
get_adapter() with no tenant= at all.

Giving adapter_helpers.py real types (PR #1721 review round 2, F5,
salesagent-2bpt.5) proved the contract was broken for these three: each called
_resolve_tenant_id_and_fallback_adapter(tenant) with the ORIGINAL,
still-unresolved tenant param instead of the ContextVar-resolved value --
'NoneType' object has no attribute 'tenant_id'.

These tests exercise the REAL adapter-resolution path end to end (real
Postgres via IntegrationEnv, real AdapterConfigRepository read, nothing
patched), mirroring tests/integration/test_capabilities_adapter_fault_injection.py's
house style.

Covers: salesagent-2bpt.5 (F5).
"""

from __future__ import annotations

import pytest

from tests.harness._base import IntegrationEnv


@pytest.mark.requires_db
class TestOptionalTenantFallsBackToContextVar:
    """tenant=None must resolve via the ContextVar, not crash on None.tenant_id."""

    def test_get_adapter_class_for_tenant_falls_back_to_contextvar(self, integration_db):
        with IntegrationEnv(tenant_id="t_ctxvar_class", principal_id="p_ctxvar_class") as env:
            tenant, _principal = env.setup_default_data()

            from src.core.config_loader import set_current_tenant
            from src.core.helpers.adapter_helpers import get_adapter_class_for_tenant

            # Verbatim shape from operations.py:233-238.
            set_current_tenant({"tenant_id": tenant.tenant_id, "ad_server": tenant.ad_server or "mock"})

            adapter_class = get_adapter_class_for_tenant(None)

        assert adapter_class.__name__ == "MockAdServer"

    def test_resolve_manual_approval_signal_falls_back_to_contextvar(self, integration_db):
        with IntegrationEnv(tenant_id="t_ctxvar_approval", principal_id="p_ctxvar_approval") as env:
            tenant, _principal = env.setup_default_data()

            from src.core.config_loader import set_current_tenant
            from src.core.helpers.adapter_helpers import resolve_manual_approval_signal

            set_current_tenant({"tenant_id": tenant.tenant_id, "ad_server": tenant.ad_server or "mock"})

            # Must not raise -- pre-fix this crashed with AttributeError on None.tenant_id.
            result = resolve_manual_approval_signal(None)

        assert result is False

    def test_get_targeting_capabilities_override_falls_back_to_contextvar(self, integration_db):
        with IntegrationEnv(tenant_id="t_ctxvar_targeting", principal_id="p_ctxvar_targeting") as env:
            tenant, _principal = env.setup_default_data()

            from src.core.config_loader import set_current_tenant
            from src.core.helpers.adapter_helpers import get_targeting_capabilities_override

            set_current_tenant({"tenant_id": tenant.tenant_id, "ad_server": tenant.ad_server or "mock"})

            # Must not raise -- pre-fix this crashed with AttributeError on None.tenant_id.
            result = get_targeting_capabilities_override(None)

        assert result is None
