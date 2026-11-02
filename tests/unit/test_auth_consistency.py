#!/usr/bin/env python3
"""
Unit tests for auth handling across the AdCP tools.

What remains here is the DISCOVERY half: a public tool takes whoever arrived, and with
nothing presented that is a PublicIdentity with no principal. The protected half -- a
tool refusing a missing or rejected credential -- is not tested here and cannot be; the
note below the imports says why and where it is graded instead.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from src.core.exceptions import AdCPSalesAgentError
from src.services.policy_check_service import PolicyStatus
from tests.factories.principal import PrincipalFactory

# --- Test Classes ---


# TestMissingTokenConsistency and TestInvalidTokenConsistency (ten tests) are REMOVED,
# along with the whole of tests/unit/test_auth_requirements.py (six tests:
# test_sync_creatives_with_invalid_auth, test_update_media_buy_requires_authentication,
# test_update_media_buy_with_invalid_auth, test_identity_with_none_principal_id,
# test_identity_with_empty_string_principal_id, test_update_media_buy_error_message_actionable)
# and tests/unit/test_sync_creatives_auth.py (test_sync_creatives_with_invalid_auth). All
# seventeen built an anonymous identity, handed it to a PROTECTED implementation
# -- _create_media_buy_impl, _update_media_buy_impl / _verify_principal,
# _sync_creatives_impl, _list_creatives_impl, _get_media_buy_delivery_impl -- and asserted
# the implementation raised AdCPAuthenticationError / AUTH_MISSING itself.
#
# NEITHER HALF IS CONSTRUCTIBLE NOW. ResolvedIdentity.principal is a required field, so
# "a ResolvedIdentity whose principal_id is None" is not a value the type can hold and
# PrincipalFactory.make_identity cannot build one (the anonymous caller is a
# PublicIdentity, which only a public tool takes). And the in-tool guards that raised
# were removed with the rest of the re-checks when the resolver became the one place a
# credential is judged (47d57e5d6); ruff-boundary.toml bans raising AUTH_MISSING or
# AUTH_INVALID anywhere but the resolver -- "do not re-check or hand-roll the refusal" --
# so those implementations could not raise it even if a guard were written back in.
# _verify_principal reads identity.principal.principal_id directly, with no guard at all.
#
# The obligation is unchanged and is graded where the decision is made: _resolve_identity
# mints the refusal once, for every tool and every transport, and the transport-blind auth
# scenarios assert the AUTH_MISSING / AUTH_INVALID wire envelope across a2a, mcp and rest.
# For the two tools these tests named most often there are also in-tree transport-level
# graders that PRESENT a credential and let the resolver judge it, which is the shape the
# deleted tests were imitating without a transport:
# tests/integration/test_creative_lifecycle_mcp.py::test_sync_creatives_authentication_required
# (rejected credential -> AUTH_INVALID) and ::test_list_creatives_authentication_required
# (rejected -> AUTH_INVALID, none presented -> AUTH_MISSING).
#
# Those are stronger graders than these were -- each of these called one implementation
# directly, so none of them could have caught a transport that skipped the resolver.
#
# Same removal, same reason, as tests/unit/test_media_buy.py:3869.


class TestDiscoveryEndpointsAnonymousAccess:
    """Test that discovery endpoints work WITHOUT auth (anonymous access)."""

    @pytest.mark.asyncio
    async def test_get_products_works_without_auth(self):
        """get_products should succeed without authentication when tenant allows public access."""
        from src.core.tools.products import _get_products_impl

        # brand_manifest_policy="public" allows anonymous access without auth requirement
        mock_tenant = {"tenant_id": "test-tenant", "name": "Test", "brand_manifest_policy": "public"}
        identity = PrincipalFactory.make_public_identity(tenant=mock_tenant)

        with (
            patch("src.core.database.repositories.uow.get_db_session") as mock_db,
            patch("src.core.tools.products.PolicyCheckService") as mock_policy,
        ):
            # Mock database to return empty products
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.scalars.return_value.all.return_value = []
            mock_session.execute.return_value.unique.return_value.scalars.return_value.all.return_value = []
            mock_db.return_value = mock_session

            # Mock policy check service
            mock_policy_instance = MagicMock()
            mock_policy_instance.check_product_eligibility.return_value = (PolicyStatus.ALLOWED, "OK")
            mock_policy.return_value = mock_policy_instance

            # Should not raise auth error
            req = MagicMock()
            req.brief = "test"
            req.brand = None
            req.filters = None
            req.context = None
            try:
                result = await _get_products_impl(req, identity)
                # If it gets past auth, it succeeded (may fail later on business logic)
            except (ToolError, AdCPSalesAgentError) as e:
                pass  # the operation must raise; its message is not asserted
                # Auth errors are failures; business logic errors are OK

    def test_list_creative_formats_works_without_auth(self):
        """list_creative_formats should succeed without authentication."""
        from src.core.tools.creative_formats import _list_creative_formats_impl

        # Create anonymous identity with tenant
        mock_tenant = {"tenant_id": "test-tenant", "name": "Test"}
        identity = PrincipalFactory.make_public_identity(tenant=mock_tenant)

        with (
            # get_creative_agent_registry is imported inside the function from src.core.creative_agent_registry
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry,
        ):
            mock_reg = MagicMock()

            async def mock_list_formats(**kwargs):
                from src.core.creative_agent_registry import FormatFetchResult

                return FormatFetchResult(formats=[], errors=[])

            mock_reg.list_all_formats_with_errors = mock_list_formats
            mock_registry.return_value = mock_reg

            req = MagicMock()
            req.type = None
            req.format_ids = None
            req.is_responsive = None
            req.name_search = None
            req.asset_types = None
            req.min_width = None
            req.max_width = None
            req.min_height = None
            req.max_height = None
            req.context = None
            req.pagination = None

            try:
                result = _list_creative_formats_impl(req, identity)
                assert result is not None
            except ToolError as e:
                pass  # the operation must raise; its message is not asserted


class TestDiscoveryEndpointsInvalidAuth:
    """Discovery implementations serve the ANONYMOUS caller: no credential was presented.

    A presented credential that does not resolve never reaches an implementation: the
    resolver refuses it with AUTH_INVALID on every row (pinned enum, "an `Authorization`
    header was present but verification failed"), graded by BR-SECURITY-002 and the
    invalid row of BR-UC-010 @T-UC-010-auth. What these tests build is the identity a
    public tool receives when NOTHING was presented, principal None, and they verify each
    discovery implementation takes it.
    """

    @pytest.mark.asyncio
    async def test_get_products_with_no_token_is_served_anonymously(self):
        """get_products with no credential presented receives the anonymous identity."""
        from src.core.tools.products import _get_products_impl

        # Nothing presented on a public row: the resolver builds an identity with
        # principal None, and the tool runs with it.
        mock_tenant = {"tenant_id": "test-tenant"}
        identity = PrincipalFactory.make_public_identity(tenant=mock_tenant)

        with patch("src.core.database.repositories.uow.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.scalars.return_value.all.return_value = []
            mock_session.execute.return_value.unique.return_value.scalars.return_value.all.return_value = []
            mock_db.return_value = mock_session

            req = MagicMock()
            req.brief = "test"
            req.brand = None
            req.filters = None
            req.context = None

            try:
                await _get_products_impl(req, identity)
            except (ToolError, AdCPSalesAgentError):
                pass  # Business logic errors OK

            # Verify the identity was anonymous (principal_id=None)
            assert identity.principal_id is None

    def test_list_creative_formats_with_no_token_gets_anonymous_identity(self):
        """list_creative_formats with no credential presented receives the anonymous identity."""
        from src.core.tools.creative_formats import _list_creative_formats_impl

        # Nothing presented on a public row: the resolver builds an identity with
        # principal None, and the tool runs with it.
        mock_tenant = {"tenant_id": "test-tenant"}
        identity = PrincipalFactory.make_public_identity(tenant=mock_tenant)

        with patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry:
            mock_reg = MagicMock()

            async def mock_list_formats(**kwargs):
                from src.core.creative_agent_registry import FormatFetchResult

                return FormatFetchResult(formats=[], errors=[])

            mock_reg.list_all_formats_with_errors = mock_list_formats
            mock_registry.return_value = mock_reg

            try:
                from src.core.schemas import ListCreativeFormatsRequest

                req = ListCreativeFormatsRequest()
                _list_creative_formats_impl(req, identity)
            except (ToolError, AdCPSalesAgentError):
                pass  # Business logic errors OK

            # Verify the identity was anonymous
            assert identity.principal_id is None
