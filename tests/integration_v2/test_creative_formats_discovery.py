"""Integration tests: UC-005-MAIN-MCP-02 authentication optional for discovery.

Covers:
- UC-005-MAIN-MCP-02: Authentication optional for discovery
- UC-005-EXT-A-01: Tenant resolution failure returns TENANT_REQUIRED error

The list_creative_formats endpoint is a discovery/catalog endpoint.
While tenant context is required to resolve the format catalog, an
explicit auth *token* should not be required. A buyer can discover
formats without presenting credentials as long as tenant context
is available.
"""

from __future__ import annotations

import pytest

from src.core.schemas import Format, FormatId, ListCreativeFormatsResponse
from tests.factories import PrincipalFactory, TenantFactory
from tests.harness import CreativeFormatsEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"


def _make_format(format_id: str, name: str) -> Format:
    """Build a minimal Format for testing."""
    from adcp.types.generated_poc.enums.format_category import FormatCategory

    return Format(
        format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id=format_id),
        name=name,
        type=FormatCategory.display,
        is_standard=True,
    )


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-02: Authentication optional for discovery
# ---------------------------------------------------------------------------


class TestAuthOptionalForDiscovery:
    """Covers: UC-005-MAIN-MCP-02 -- auth token not required for format discovery."""

    def test_impl_returns_formats_with_no_auth_token(self, integration_db):
        """UC-005-MAIN-MCP-02: _impl succeeds when identity has no auth_token.

        Given an identity with tenant context but auth_token=None,
        When calling _list_creative_formats_impl,
        Then the response is a valid ListCreativeFormatsResponse with formats.
        """
        formats = [
            _make_format("fmt_1", "Display Banner"),
            _make_format("fmt_2", "Leaderboard"),
        ]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            identity_no_token = PrincipalFactory.make_identity(
                principal_id="anon_buyer",
                tenant_id="test_tenant",
                protocol="mcp",
                auth_token=None,
            )
            response = env.call_impl(identity=identity_no_token)

        assert isinstance(response, ListCreativeFormatsResponse)
        assert len(response.formats) == 2
        ids = {f.format_id.id for f in response.formats}
        assert ids == {"fmt_1", "fmt_2"}

    def test_a2a_returns_formats_with_no_auth_token(self, integration_db):
        """UC-005-MAIN-MCP-02: A2A wrapper succeeds without auth_token.

        Given an identity with tenant context but auth_token=None,
        When calling list_creative_formats_raw (A2A),
        Then the response is a valid ListCreativeFormatsResponse.
        """
        formats = [_make_format("a2a_fmt", "A2A Display")]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            identity_no_token = PrincipalFactory.make_identity(
                principal_id="anon_buyer",
                tenant_id="test_tenant",
                protocol="a2a",
                auth_token=None,
            )
            response = env.call_a2a(identity=identity_no_token)

        assert isinstance(response, ListCreativeFormatsResponse)
        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "a2a_fmt"

    def test_impl_with_no_auth_token_via_call_via(self, integration_db):
        """UC-005-MAIN-MCP-02: call_via(IMPL) with explicit no-token identity.

        Uses the multi-transport dispatch path with an unauthenticated identity
        to verify the TransportResult wrapper also succeeds.
        """
        formats = [_make_format("dispatch_fmt", "Dispatched Format")]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            identity_no_token = PrincipalFactory.make_identity(
                principal_id="anon_buyer",
                tenant_id="test_tenant",
                protocol="mcp",
                auth_token=None,
            )
            result = env.call_via(Transport.IMPL, identity=identity_no_token)

        assert result.is_success
        assert isinstance(result.payload, ListCreativeFormatsResponse)
        assert len(result.payload.formats) == 1

    def test_a2a_with_no_auth_token_via_call_via(self, integration_db):
        """UC-005-MAIN-MCP-02: call_via(A2A) with explicit no-token identity.

        Verifies A2A transport dispatch succeeds without auth token.
        """
        formats = [_make_format("a2a_dispatch", "A2A Dispatch Format")]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            identity_no_token = PrincipalFactory.make_identity(
                principal_id="anon_buyer",
                tenant_id="test_tenant",
                protocol="a2a",
                auth_token=None,
            )
            result = env.call_via(Transport.A2A, identity=identity_no_token)

        assert result.is_success
        assert isinstance(result.payload, ListCreativeFormatsResponse)
        assert len(result.payload.formats) == 1

    def test_no_tenant_context_raises_auth_error(self, integration_db):
        """UC-005-MAIN-MCP-02: missing tenant context IS an error, even though auth is optional.

        Authentication is optional for discovery, but tenant context is still
        required to resolve which format catalog to return. When tenant=None,
        AdCPAuthenticationError is raised.
        """
        from src.core.exceptions import AdCPAuthenticationError

        formats = [_make_format("no_tenant_fmt", "Should Not Reach")]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            identity_no_tenant = PrincipalFactory.make_identity(
                principal_id="anon_buyer",
                tenant_id="orphan",
                tenant=None,
                protocol="mcp",
                auth_token=None,
            )
            result = env.call_via(Transport.IMPL, identity=identity_no_tenant)

        assert result.is_error
        assert isinstance(result.error, AdCPAuthenticationError)

    def test_authenticated_vs_unauthenticated_return_same_catalog(self, integration_db):
        """UC-005-MAIN-MCP-02: auth token does not affect the catalog returned.

        Both an authenticated and unauthenticated identity with the same
        tenant context should receive identical format catalogs.
        """
        formats = [
            _make_format("shared_1", "Shared Format 1"),
            _make_format("shared_2", "Shared Format 2"),
        ]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            authed_identity = PrincipalFactory.make_identity(
                principal_id="authed_buyer",
                tenant_id="test_tenant",
                protocol="mcp",
                auth_token="valid-token-123",
            )
            unauthed_identity = PrincipalFactory.make_identity(
                principal_id="anon_buyer",
                tenant_id="test_tenant",
                protocol="mcp",
                auth_token=None,
            )

            authed_response = env.call_impl(identity=authed_identity)
            unauthed_response = env.call_impl(identity=unauthed_identity)

        assert len(authed_response.formats) == len(unauthed_response.formats)
        authed_ids = {f.format_id.id for f in authed_response.formats}
        unauthed_ids = {f.format_id.id for f in unauthed_response.formats}
        assert authed_ids == unauthed_ids


# ---------------------------------------------------------------------------
# UC-005-EXT-A-01: Tenant resolution failure
# ---------------------------------------------------------------------------


class TestTenantResolutionFailure:
    """Covers: UC-005-EXT-A-01

    Given no auth token AND no hostname mapping resolves to a tenant,
    When Buyer calls list_creative_formats,
    Then error with tenant context message and suggestion to provide credentials.
    """

    def test_no_tenant_no_auth_raises_auth_error(self, integration_db):
        """UC-005-EXT-A-01: tenant=None + auth_token=None -> AdCPAuthenticationError."""
        from src.core.exceptions import AdCPAuthenticationError

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([_make_format("unreachable", "Should Not Reach")])

            identity = PrincipalFactory.make_identity(
                principal_id="anon_buyer",
                tenant_id="unknown",
                tenant=None,
                protocol="mcp",
                auth_token=None,
            )
            result = env.call_via(Transport.IMPL, identity=identity)

        assert result.is_error
        assert isinstance(result.error, AdCPAuthenticationError)

    def test_error_message_mentions_tenant(self, integration_db):
        """UC-005-EXT-A-01: error message indicates tenant context could not be determined."""
        from src.core.exceptions import AdCPAuthenticationError

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([])

            identity = PrincipalFactory.make_identity(
                principal_id="anon_buyer",
                tenant_id="unknown",
                tenant=None,
                protocol="a2a",
                auth_token=None,
            )
            result = env.call_via(Transport.A2A, identity=identity)

        assert result.is_error
        assert isinstance(result.error, AdCPAuthenticationError)
        assert "tenant" in str(result.error).lower()
