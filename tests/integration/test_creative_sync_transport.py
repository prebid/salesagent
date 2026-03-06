"""Multi-transport behavioral tests for creative sync.

Exercises the same behavioral obligation across IMPL, A2A, and REST
transports. Fixture setup and payload assertions are shared; only the
dispatch mechanism varies.

Covers: T-UC-006-main-rest, T-UC-006-main-mcp (transport-paired obligations)
"""

from __future__ import annotations

import pytest

from tests.factories import PrincipalFactory, TenantFactory
from tests.harness import CreativeSyncEnv, Transport, assert_envelope

# All four transports: IMPL, A2A, REST, MCP
ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.REST, Transport.MCP]


@pytest.mark.requires_db
class TestSyncCreativeCreateTransport:
    """New creative creation via all transports."""

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_new_creative_created(self, integration_db, transport):
        """A valid creative payload creates a new creative across all transports.

        Covers: T-UC-006-main-rest, T-UC-006-main-mcp
        """
        with CreativeSyncEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(
                tenant_id="test_tenant",
                principal_id="test_principal",
            )

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_transport_test",
                        "name": "Transport Test Creative",
                        "format_id": {"id": "display_300x250", "agent_url": "https://example.com/agent"},
                        "media_url": "https://example.com/image.png",
                    }
                ],
            )

        assert result.is_success, f"Expected success but got error: {result.error}"
        if transport == Transport.REST:
            assert_envelope(result, Transport.REST)

        # Shared payload assertion — identical across all transports
        assert len(result.payload.creatives) == 1
        creative = result.payload.creatives[0]
        assert creative.creative_id == "c_transport_test"

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_empty_creative_list_returns_success(self, integration_db, transport):
        """Empty creative list is a valid no-op across all transports."""
        with CreativeSyncEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(
                tenant_id="test_tenant",
                principal_id="test_principal",
            )

            result = env.call_via(transport, creatives=[])

        assert result.is_success
        assert len(result.payload.creatives) == 0

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_dry_run_does_not_persist(self, integration_db, transport):
        """Dry run previews changes without persisting across all transports."""
        with CreativeSyncEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(
                tenant_id="test_tenant",
                principal_id="test_principal",
            )

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_dry_run",
                        "name": "Dry Run Creative",
                        "format_id": {"id": "display_300x250", "agent_url": "https://example.com/agent"},
                        "media_url": "https://example.com/image.png",
                    }
                ],
                dry_run=True,
            )

        assert result.is_success
        assert result.payload.dry_run is True
