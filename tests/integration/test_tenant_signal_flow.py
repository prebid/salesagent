"""Tenant-authored signal discovery and activation."""

from __future__ import annotations

import asyncio

import pytest

from src.core.exceptions import AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import GetSignalsRequest
from src.core.tools.signals import _activate_signal_impl, _get_signals_impl
from tests.factories import PrincipalFactory, TenantFactory, TenantSignalFactory
from tests.utils.database_helpers import _bind_factories_to_session

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _identity(tenant_id: str, principal_id: str | None = None) -> ResolvedIdentity:
    return ResolvedIdentity(
        tenant_id=tenant_id,
        principal_id=principal_id,
        tenant={
            "tenant_id": tenant_id,
            "ad_server": "google_ad_manager",
            "public_agent_url": f"https://{tenant_id}.example.com/agent",
        },
        principal=None,
        auth_method="api_key",
        raw_credential=None,
    )


def test_tenant_signal_appears_in_get_signals(integration_db):
    from src.core.database.database_session import get_db_session

    with get_db_session() as session, _bind_factories_to_session(session):
        tenant = TenantFactory(
            tenant_id="sig_disc_t1",
            ad_server="google_ad_manager",
        )
        TenantSignalFactory(
            tenant=tenant,
            signal_id="audience_sports_fans",
            name="Sports Fans",
            value_type="binary",
            adapter_config={"kind": "audience_segment", "segment_id": "98765"},
            targeting_dimension="audience",
            data_provider="publisher_1p",
        )

    response = asyncio.run(_get_signals_impl(GetSignalsRequest(), identity=_identity("sig_disc_t1")))

    target = [s for s in response.signals if s.signal_agent_segment_id == "audience_sports_fans"]
    assert len(target) == 1
    wire = target[0].model_dump(mode="json")
    assert wire["value_type"] == "binary"
    assert wire["data_provider"] == "publisher_1p"
    assert "adapter_config" not in wire


def test_tenant_signal_dot_id_is_canonicalized_for_wire_and_activation(integration_db):
    from src.core.database.database_session import get_db_session

    with get_db_session() as session, _bind_factories_to_session(session):
        tenant = TenantFactory(tenant_id="sig_dot_t1", ad_server="google_ad_manager")
        principal = PrincipalFactory(tenant=tenant)
        TenantSignalFactory(
            tenant=tenant,
            signal_id="audience.sports_fans",
            name="Sports Fans",
            value_type="binary",
            adapter_config={"kind": "audience_segment", "segment_id": "98765"},
        )
        principal_id = principal.principal_id

    signals = asyncio.run(_get_signals_impl(GetSignalsRequest(), identity=_identity("sig_dot_t1")))
    discovered_signal_ids = {signal.signal_agent_segment_id for signal in signals.signals}
    assert "audience_sports_fans" in discovered_signal_ids

    activated = asyncio.run(
        _activate_signal_impl(
            signal_agent_segment_id="audience_sports_fans",
            identity=_identity("sig_dot_t1", principal_id),
        )
    )
    assert activated.errors is None
    assert activated.activation_details is not None
    assert activated.activation_details["status"] == "processing"


def test_activate_undeclared_signal_raises(integration_db):
    from src.core.database.database_session import get_db_session

    with get_db_session() as session, _bind_factories_to_session(session):
        tenant = TenantFactory(tenant_id="sig_unknown_t1", ad_server="google_ad_manager")
        principal = PrincipalFactory(tenant=tenant)
        principal_id = principal.principal_id

    with pytest.raises(AdCPValidationError) as exc_info:
        asyncio.run(
            _activate_signal_impl(
                signal_agent_segment_id="unknown_signal",
                identity=_identity("sig_unknown_t1", principal_id),
            )
        )

    assert "unknown_signal" in str(exc_info.value)
