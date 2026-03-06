"""Multi-transport behavioral tests for creative sync.

Exercises the same behavioral obligation across IMPL, A2A, REST, and MCP
transports. Fixture setup and payload assertions are shared; only the
dispatch mechanism varies.

Covers: UC-006-MAIN-MCP-04 through UC-006-MAIN-MCP-09 (transport-paired)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from adcp.types import CreativeAction

from src.core.exceptions import AdCPNotFoundError
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


DEFAULT_AGENT_URL = "https://example.com/agent"
DEFAULT_FORMAT_ID = {"id": "display_300x250", "agent_url": DEFAULT_AGENT_URL}


def _creative(creative_id: str = "c1", name: str = "Test", **overrides) -> dict:
    """Build a minimal creative dict for transport tests."""
    defaults = {
        "creative_id": creative_id,
        "name": name,
        "format_id": DEFAULT_FORMAT_ID,
        "assets": {"banner": {"url": "https://example.com/image.png"}},
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.requires_db
class TestSyncUpsertReturnsUpdatedTransport:
    """Re-syncing an existing creative returns action="updated" with changes list.

    Covers: UC-006-MAIN-MCP-04
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_upsert_existing_creative_reports_updated(self, integration_db, transport):
        """Syncing a creative that already exists returns action=updated."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # First sync: create the creative
            creative_data = _creative(creative_id="c_upsert")
            env.call_via(Transport.IMPL, creatives=[creative_data])

            # Second sync via parametrized transport: upsert
            result = env.call_via(transport, creatives=[creative_data])

        assert result.is_success, f"Expected success but got error: {result.error}"
        assert len(result.payload.creatives) == 1
        upserted = result.payload.creatives[0]
        assert upserted.creative_id == "c_upsert"
        assert upserted.action == CreativeAction.updated


@pytest.mark.requires_db
class TestSyncSavepointIsolationTransport:
    """Good creatives persist even when another creative in the batch fails.

    Covers: UC-006-MAIN-MCP-05
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_good_creative_persists_despite_bad_in_batch(self, integration_db, transport):
        """Savepoint isolation: bad creative doesn't roll back good ones."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            result = env.call_via(
                transport,
                creatives=[
                    _creative(creative_id="c_good_1", name="Good One"),
                    _creative(creative_id="c_bad", name=""),  # empty name → validation fail
                    _creative(creative_id="c_good_2", name="Good Two"),
                ],
                validation_mode="lenient",
            )

        assert result.is_success
        assert len(result.payload.creatives) == 3

        results_by_id = {r.creative_id: r for r in result.payload.creatives}
        assert results_by_id["c_bad"].action == CreativeAction.failed
        assert results_by_id["c_good_1"].action != CreativeAction.failed
        assert results_by_id["c_good_2"].action != CreativeAction.failed


@pytest.mark.requires_db
class TestSyncStrictModeAbortTransport:
    """Strict mode aborts the assignment phase on missing package.

    Covers: UC-006-MAIN-MCP-06
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_strict_mode_missing_package_aborts(self, integration_db, transport):
        """Strict validation_mode raises on missing package assignment."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            result = env.call_via(
                transport,
                creatives=[_creative(creative_id="c_strict", name="Strict Test")],
                assignments={"c_strict": ["PKG-NONEXISTENT"]},
                validation_mode="strict",
            )

        assert result.is_error, "Strict mode should error on missing package"
        assert isinstance(result.error, AdCPNotFoundError)


@pytest.mark.requires_db
class TestSyncLenientModeContinuesTransport:
    """Lenient mode records assignment errors without aborting.

    Covers: UC-006-MAIN-MCP-07
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_lenient_mode_missing_package_records_error(self, integration_db, transport):
        """Lenient validation_mode logs error and continues past missing package."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            result = env.call_via(
                transport,
                creatives=[_creative(creative_id="c_lenient", name="Lenient Test")],
                assignments={"c_lenient": ["PKG-MISSING"]},
                validation_mode="lenient",
            )

        assert result.is_success
        assert len(result.payload.creatives) == 1
        creative_result = result.payload.creatives[0]
        assert creative_result.assignment_errors is not None
        assert "PKG-MISSING" in creative_result.assignment_errors


@pytest.mark.requires_db
class TestSyncFormatValidationTransport:
    """Format validation runs before DB writes — unknown format → failed.

    Covers: UC-006-MAIN-MCP-08
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_unknown_format_fails_before_db_write(self, integration_db, transport):
        """Creative with unknown format_id gets action=failed."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Override: registry.get_format returns None (format not found)
            registry_mock = env.mock["registry"].return_value
            registry_mock.get_format = AsyncMock(return_value=None)

            result = env.call_via(
                transport,
                creatives=[_creative(creative_id="c_bad_fmt", name="Bad Format")],
            )

        assert result.is_success  # sync itself succeeds, individual creative fails
        assert len(result.payload.creatives) == 1
        creative_result = result.payload.creatives[0]
        assert creative_result.action == CreativeAction.failed
        assert any("list_creative_formats" in e for e in (creative_result.errors or []))


@pytest.mark.requires_db
class TestSyncRegistryCachingTransport:
    """Registry is queried once per sync, not per creative.

    Covers: UC-006-MAIN-MCP-09
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_registry_called_once_for_multiple_creatives(self, integration_db, transport):
        """list_all_formats is called once regardless of creative count."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            result = env.call_via(
                transport,
                creatives=[
                    _creative(creative_id="c_cache_1", name="Cache Test 1"),
                    _creative(creative_id="c_cache_2", name="Cache Test 2"),
                    _creative(creative_id="c_cache_3", name="Cache Test 3"),
                ],
            )

            assert result.is_success
            # run_async_in_sync_context is called once (for list_all_formats)
            assert env.mock["run_async"].call_count == 1
