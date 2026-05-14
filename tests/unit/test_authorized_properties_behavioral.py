"""Behavioral tests for list_authorized_properties (UC-007).

Tests HIGH_RISK and MEDIUM_RISK gaps identified in the BDD scenario catalog.
Each test traces a real scenario through _list_authorized_properties_impl or
the MCP wrapper list_authorized_properties.

HIGH_RISK tests (H1-H7):
  H1. TENANT_ERROR path
  H2. PROPERTIES_ERROR path
  H3. Advertising policy assembly (all 5 sections)
  H4. Advertising policy partial sections
  H5. Advertising policy empty arrays suppressed
  H6. Advertising policy enforcement footer
  H7. MCP wrapper header extraction

MEDIUM_RISK tests (M1-M7):
  M1. Context echo with value
  M2. Context echo with empty portfolio
  M3. Context echo when None
  M4. Context echo complex nested
  M5. Audit log on success
  M6. Audit log on failure
  M7. Advertising policy omitted when disabled
"""

from unittest.mock import MagicMock, patch

import pytest
from adcp.types.generated_poc.core.context import ContextObject

from src.core.exceptions import AdCPAdapterError, AdCPAuthenticationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import ListAuthorizedPropertiesRequest, ListAuthorizedPropertiesResponse

# --- Helpers ---


def _make_mock_tenant(tenant_id="test-tenant", name="Test Tenant", advertising_policy=None):
    """Build a tenant dict matching the shape used by ResolvedIdentity."""
    tenant = {"tenant_id": tenant_id, "name": name}
    if advertising_policy is not None:
        tenant["advertising_policy"] = advertising_policy
    return tenant


def _make_identity(tenant, principal_id=None):
    """Build a ResolvedIdentity from a tenant dict and optional principal_id."""
    if tenant is None:
        return ResolvedIdentity(principal_id=principal_id, tenant=None, protocol="mcp")
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant.get("tenant_id"),
        tenant=tenant,
        protocol="mcp",
    )


def _make_publisher(domain):
    """Build a mock PublisherPartner row."""
    pub = MagicMock()
    pub.publisher_domain = domain
    return pub


def _patch_impl_dependencies(
    tenant,
    publishers=None,
    db_side_effect=None,
):
    """Return a dict of patches for _list_authorized_properties_impl dependencies.

    Args:
        tenant: The tenant dict (or None to simulate TENANT_ERROR).
        publishers: List of mock PublisherPartner objects.
        db_side_effect: If set, TenantConfigUoW context manager body raises this.
    """
    patches = {
        "audit": patch("src.core.tools.properties.get_audit_logger"),
        "log_activity": patch("src.core.tools.properties.log_tool_activity"),
    }

    # Build mock TenantConfigUoW
    mock_repo = MagicMock()
    if db_side_effect:
        mock_repo.list_publisher_partners.side_effect = db_side_effect
    else:
        mock_repo.list_publisher_partners.return_value = publishers or []

    mock_uow = MagicMock()
    mock_uow.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow.__exit__ = MagicMock(return_value=False)
    mock_uow.tenant_config = mock_repo

    patches["db"] = patch(
        "src.core.tools.properties.TenantConfigUoW",
        return_value=mock_uow,
    )

    return patches


# ===========================================================================
# HIGH_RISK: H1 — TENANT_ERROR path (scenarios 6, 7)
# ===========================================================================


class TestTenantErrorPath:
    """When identity has no tenant and no current tenant exists, AdCPAuthenticationError is raised."""

    def test_tenant_error_when_no_tenant_resolvable(self):
        """H1: No tenant from identity and no current tenant raises AdCPAuthenticationError."""
        from src.core.tools.properties import _list_authorized_properties_impl

        with (
            patch("src.core.tools.properties.log_tool_activity"),
        ):
            with pytest.raises(AdCPAuthenticationError, match="Could not resolve tenant"):
                _list_authorized_properties_impl(req=None, identity=None)

    def test_tenant_error_message_is_descriptive(self):
        """H1: Error message mentions subdomain, virtual host, or header."""
        from src.core.tools.properties import _list_authorized_properties_impl

        with (
            patch("src.core.tools.properties.log_tool_activity"),
        ):
            with pytest.raises(AdCPAuthenticationError, match="subdomain|virtual host|x-adcp-tenant"):
                _list_authorized_properties_impl(req=None, identity=None)


# ===========================================================================
# HIGH_RISK: H2 — PROPERTIES_ERROR path (scenarios 8, 9)
# ===========================================================================


class TestPropertiesErrorPath:
    """When the database query raises an exception, AdCPAdapterError is raised."""

    def test_properties_error_on_db_exception(self):
        """H2: Database exception in _impl raises PROPERTIES_ERROR."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant()
        identity = _make_identity(tenant)

        patches = _patch_impl_dependencies(
            tenant=tenant,
            db_side_effect=RuntimeError("connection lost"),
        )
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            with pytest.raises(AdCPAdapterError, match="Failed to list authorized properties"):
                _list_authorized_properties_impl(req=None, identity=identity)

    def test_properties_error_calls_audit_with_failure(self):
        """H2: PROPERTIES_ERROR path logs audit with success=False."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant()
        identity = _make_identity(tenant)

        patches = _patch_impl_dependencies(
            tenant=tenant,
            db_side_effect=RuntimeError("connection lost"),
        )
        with (
            patches["db"],
            patches["audit"] as mock_get_audit,
            patches["log_activity"],
        ):
            mock_audit_instance = MagicMock()
            mock_get_audit.return_value = mock_audit_instance

            with pytest.raises(AdCPAdapterError, match="Failed to list authorized properties"):
                _list_authorized_properties_impl(req=None, identity=identity)

            mock_audit_instance.log_operation.assert_called_once()
            call_kwargs = mock_audit_instance.log_operation.call_args
            assert call_kwargs[1]["success"] is False or call_kwargs.kwargs.get("success") is False
            # Verify error string is passed
            _, kwargs = mock_audit_instance.log_operation.call_args
            assert kwargs.get("success") is False
            assert "connection lost" in kwargs.get("error", "")


# ===========================================================================
# HIGH_RISK: H3 — Advertising policy assembly with all 5 sections (scenario 25)
# ===========================================================================


class TestAdvertisingPolicyAssemblyFull:
    """When tenant has advertising_policy.enabled=True with all 5 sections populated."""

    def _make_full_policy_tenant(self):
        return _make_mock_tenant(
            advertising_policy={
                "enabled": True,
                "default_prohibited_categories": ["Alcohol", "Gambling"],
                "default_prohibited_tactics": ["Pop-ups", "Auto-play audio"],
                "prohibited_categories": ["Tobacco", "Weapons"],
                "prohibited_tactics": ["Deceptive redirects"],
                "prohibited_advertisers": ["shady-ads.com", "spam-network.org"],
            }
        )

    def test_all_five_sections_present(self):
        """H3: All 5 policy sections appear in advertising_policies text."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = self._make_full_policy_tenant()
        identity = _make_identity(tenant)
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=None, identity=identity)

        policy = result.advertising_policies
        assert policy is not None

        # All 5 section headers must be present
        assert "Baseline Protected Categories" in policy
        assert "Baseline Prohibited Tactics" in policy
        assert "Additional Prohibited Categories" in policy
        assert "Additional Prohibited Tactics" in policy
        assert "Blocked Advertisers/Domains" in policy

        # All values must appear
        assert "Alcohol" in policy
        assert "Gambling" in policy
        assert "Pop-ups" in policy
        assert "Auto-play audio" in policy
        assert "Tobacco" in policy
        assert "Weapons" in policy
        assert "Deceptive redirects" in policy
        assert "shady-ads.com" in policy
        assert "spam-network.org" in policy

    def test_enforcement_footer_at_end(self):
        """H3: Full policy text ends with enforcement footer."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = self._make_full_policy_tenant()
        identity = _make_identity(tenant)
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=None, identity=identity)

        policy = result.advertising_policies
        assert policy is not None
        assert "Policy Enforcement:" in policy
        # Footer is the last section
        last_section = policy.split("\n\n")[-1]
        assert last_section.startswith("**Policy Enforcement:")


# ===========================================================================
# HIGH_RISK: H4 — Advertising policy partial sections (scenarios 29, 30)
# ===========================================================================


class TestAdvertisingPolicyPartialSections:
    """When tenant has only some policy sections configured."""

    def test_only_categories_configured(self):
        """H4 (scenario 29): Only default_prohibited_categories configured."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant(
            advertising_policy={
                "enabled": True,
                "default_prohibited_categories": ["Alcohol"],
            }
        )
        identity = _make_identity(tenant)
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=None, identity=identity)

        policy = result.advertising_policies
        assert policy is not None
        assert "Baseline Protected Categories" in policy
        assert "Alcohol" in policy
        # Other sections should NOT appear
        assert "Baseline Prohibited Tactics" not in policy
        assert "Additional Prohibited Categories" not in policy
        assert "Additional Prohibited Tactics" not in policy
        assert "Blocked Advertisers/Domains" not in policy

    def test_only_blocked_advertisers_configured(self):
        """H4 (scenario 30): Only prohibited_advertisers configured."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant(
            advertising_policy={
                "enabled": True,
                "prohibited_advertisers": ["bad-actor.com"],
            }
        )
        identity = _make_identity(tenant)
        publishers = [_make_publisher("news.example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=None, identity=identity)

        policy = result.advertising_policies
        assert policy is not None
        assert "Blocked Advertisers/Domains" in policy
        assert "bad-actor.com" in policy
        # Other sections should NOT appear
        assert "Baseline Protected Categories" not in policy
        assert "Baseline Prohibited Tactics" not in policy
        assert "Additional Prohibited Categories" not in policy
        assert "Additional Prohibited Tactics" not in policy


# ===========================================================================
# HIGH_RISK: H5 — Advertising policy empty arrays suppressed (scenario 27)
# ===========================================================================


class TestAdvertisingPolicyEmptyArraysSuppressed:
    """When tenant has enabled=True but all policy arrays are empty."""

    def test_empty_arrays_produce_no_policy(self):
        """H5: enabled=True with all empty arrays => advertising_policies is None."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant(
            advertising_policy={
                "enabled": True,
                "default_prohibited_categories": [],
                "default_prohibited_tactics": [],
                "prohibited_categories": [],
                "prohibited_tactics": [],
                "prohibited_advertisers": [],
            }
        )
        identity = _make_identity(tenant)
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=None, identity=identity)

        # advertising_policies should not be set (None or absent from dump)
        assert result.advertising_policies is None
        data = result.model_dump()
        assert "advertising_policies" not in data

    def test_enabled_true_with_missing_keys_no_policy(self):
        """H5 variant: enabled=True but no policy keys present at all."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant(
            advertising_policy={
                "enabled": True,
            }
        )
        identity = _make_identity(tenant)
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=None, identity=identity)

        assert result.advertising_policies is None


# ===========================================================================
# HIGH_RISK: H6 — Advertising policy enforcement footer (scenario 28)
# ===========================================================================


class TestAdvertisingPolicyEnforcementFooter:
    """When any policy section is populated, footer text starts with 'Policy Enforcement:'."""

    @pytest.mark.parametrize(
        "policy_key,policy_value",
        [
            ("default_prohibited_categories", ["Alcohol"]),
            ("default_prohibited_tactics", ["Auto-play"]),
            ("prohibited_categories", ["Weapons"]),
            ("prohibited_tactics", ["Cloaking"]),
            ("prohibited_advertisers", ["spam.com"]),
        ],
        ids=[
            "baseline-categories",
            "baseline-tactics",
            "additional-categories",
            "additional-tactics",
            "blocked-advertisers",
        ],
    )
    def test_footer_present_for_each_section(self, policy_key, policy_value):
        """H6: Each individual section triggers the enforcement footer."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant(
            advertising_policy={
                "enabled": True,
                policy_key: policy_value,
            }
        )
        identity = _make_identity(tenant)
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=None, identity=identity)

        policy = result.advertising_policies
        assert policy is not None
        assert "**Policy Enforcement:**" in policy


# ===========================================================================
# HIGH_RISK: H7 — MCP wrapper header extraction (scenario 2)
# ===========================================================================


class TestMCPWrapperIdentityResolution:
    """The MCP wrapper list_authorized_properties reads identity from ctx.get_state (set by middleware)."""

    _stub_response = ListAuthorizedPropertiesResponse(publisher_domains=["stub.com"])

    async def test_resolves_identity_from_context(self):
        """H7: MCP wrapper reads identity from ctx.get_state and passes it to _impl."""
        from unittest.mock import AsyncMock

        from fastmcp.server.context import Context

        from src.core.tools.properties import list_authorized_properties

        mock_ctx = MagicMock(spec=Context)
        mock_identity = _make_identity(_make_mock_tenant(), principal_id="user-1")
        mock_ctx.get_state = AsyncMock(return_value=mock_identity)

        with patch("src.core.tools.properties._list_authorized_properties_impl") as mock_impl:
            mock_impl.return_value = self._stub_response
            await list_authorized_properties(ctx=mock_ctx)

            # Verify get_state was called to retrieve identity
            mock_ctx.get_state.assert_called_once_with("identity")
            # Verify _impl received the resolved identity
            mock_impl.assert_called_once()
            passed_identity = mock_impl.call_args[0][1]
            assert passed_identity is mock_identity

    async def test_passes_none_identity_when_no_ctx(self):
        """H7: MCP wrapper handles ctx=None by passing None identity."""
        from src.core.tools.properties import list_authorized_properties

        with patch("src.core.tools.properties._list_authorized_properties_impl") as mock_impl:
            mock_impl.return_value = self._stub_response
            await list_authorized_properties(ctx=None)

            mock_impl.assert_called_once()
            passed_identity = mock_impl.call_args[0][1]
            assert passed_identity is None

    async def test_wrapper_returns_tool_result(self):
        """H7: MCP wrapper returns a ToolResult with structured_content."""
        from fastmcp.tools.tool import ToolResult

        from src.core.tools.properties import list_authorized_properties

        with patch("src.core.tools.properties._list_authorized_properties_impl") as mock_impl:
            mock_impl.return_value = self._stub_response
            result = await list_authorized_properties(ctx=None)

            assert isinstance(result, ToolResult)
            # structured_content may be the response object or its dict representation
            sc = result.structured_content
            if isinstance(sc, dict):
                assert sc["publisher_domains"] == ["stub.com"]
            else:
                assert sc.publisher_domains == ["stub.com"]


# ===========================================================================
# BUG: MCP wrapper drops context parameter (salesagent-pdnu)
# ===========================================================================


class TestMCPWrapperContextEcho:
    """Bug: The MCP wrapper receives context as a separate param but never injects it into the request.

    When an MCP client sends context as a top-level param, the wrapper receives it as `context`
    but passes `req` (which may be None or lack context) to _impl. The _impl echoes req.context,
    which is None, violating the AdCP spec requirement to echo context back.
    """

    async def test_mcp_wrapper_propagates_context_to_impl(self):
        """Bug salesagent-pdnu: context passed to MCP wrapper must appear in response.

        This is the actual bug path: MCP client sends context={...} as a top-level
        parameter. The wrapper receives it but does not inject it into req before
        calling _impl. Result: response.context is None instead of the caller's context.
        """
        from unittest.mock import AsyncMock

        from fastmcp.server.context import Context

        from src.core.tools.properties import list_authorized_properties

        test_context = ContextObject(e2e="list_authorized_properties", session="test-456")
        mock_identity = _make_identity(_make_mock_tenant(), principal_id="user-1")
        tenant = _make_mock_tenant()
        publishers = [_make_publisher("example.com")]

        # Provide identity via mock ctx (as middleware would)
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=mock_identity)

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = await list_authorized_properties(context=test_context, ctx=mock_ctx)

            # The structured_content in the ToolResult should contain the echoed context
            sc = result.structured_content
            if isinstance(sc, dict):
                assert sc.get("context") is not None, "Context should be echoed back in response. Got context=None"
            else:
                assert sc.context is not None, "Context should be echoed back in response. Got context=None"
                assert sc.context == test_context

    async def test_mcp_wrapper_context_with_existing_req(self):
        """When both req and context are provided, context should override req.context."""
        from unittest.mock import AsyncMock

        from fastmcp.server.context import Context

        from src.core.tools.properties import list_authorized_properties

        # req has no context, but wrapper receives context as separate param
        req = ListAuthorizedPropertiesRequest()
        test_context = ContextObject(session="override-test")
        mock_identity = _make_identity(_make_mock_tenant(), principal_id="user-1")
        tenant = _make_mock_tenant()
        publishers = [_make_publisher("example.com")]

        # Provide identity via mock ctx (as middleware would)
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=mock_identity)

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = await list_authorized_properties(context=test_context, ctx=mock_ctx)

            sc = result.structured_content
            if isinstance(sc, dict):
                assert sc.get("context") is not None, "Context should be echoed"
            else:
                assert sc.context is not None, "Context should be echoed"
                assert sc.context == test_context


# ===========================================================================
# MEDIUM_RISK: M1 — Context echo with value (scenario 21)
# ===========================================================================


class TestContextEchoWithValue:
    """When req.context has a value, it is echoed in the response."""

    def test_context_echoed_with_publishers(self):
        """M1: Context from request appears in response with non-empty portfolio."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant()
        identity = _make_identity(tenant)
        publishers = [_make_publisher("example.com"), _make_publisher("news.com")]

        req_context = ContextObject(campaign_id="abc-123", session="sess-1")
        req = ListAuthorizedPropertiesRequest(context=req_context)

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=req, identity=identity)

        assert result.context is not None
        assert result.context == req_context


# ===========================================================================
# MEDIUM_RISK: M2 — Context echo with empty portfolio (scenario 23)
# ===========================================================================


class TestContextEchoEmptyPortfolio:
    """When no publishers exist, context is still echoed."""

    def test_context_echoed_on_empty_portfolio(self):
        """M2: Empty portfolio response still echoes context."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant()
        identity = _make_identity(tenant)

        req_context = ContextObject(session="xyz")
        req = ListAuthorizedPropertiesRequest(context=req_context)

        patches = _patch_impl_dependencies(tenant=tenant, publishers=[])
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=req, identity=identity)

        assert result.publisher_domains == []
        assert result.context is not None
        assert result.context == req_context


# ===========================================================================
# MEDIUM_RISK: M3 — Context echo when None (scenario 22)
# ===========================================================================


class TestContextEchoNone:
    """When req.context is None, response.context is None."""

    def test_no_context_in_request(self):
        """M3: req.context=None => response.context is None."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant()
        identity = _make_identity(tenant)
        publishers = [_make_publisher("example.com")]

        req = ListAuthorizedPropertiesRequest()  # context defaults to None

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=req, identity=identity)

        assert result.context is None
        data = result.model_dump()
        assert "context" not in data

    def test_none_req_produces_none_context(self):
        """M3: req=None => response.context is None."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant()
        identity = _make_identity(tenant)
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=None, identity=identity)

        assert result.context is None


# ===========================================================================
# MEDIUM_RISK: M4 — Context echo complex nested (scenario 24)
# ===========================================================================


class TestContextEchoComplexNested:
    """Complex nested context is preserved exactly."""

    def test_deeply_nested_context_preserved(self):
        """M4: Nested dict with lists is echoed faithfully."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant()
        identity = _make_identity(tenant)
        publishers = [_make_publisher("example.com")]

        nested_context = ContextObject(
            campaign_id="camp-1",
            metadata={"tags": ["premium", "video"], "geo": {"country": "US", "regions": ["NY", "CA"]}},
            items=[1, 2, 3],
        )
        req = ListAuthorizedPropertiesRequest(context=nested_context)

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=req, identity=identity)

        assert result.context is not None
        # ContextObject preserves via reference assignment, so it should be identical
        assert result.context is req.context


# ===========================================================================
# MEDIUM_RISK: M5 — Audit log on success (scenario 5)
# ===========================================================================


class TestAuditLogSuccess:
    """Successful property listing logs audit with correct details."""

    def test_audit_called_on_success(self):
        """M5: audit_logger.log_operation called with success=True and publisher details."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant()
        identity = _make_identity(tenant, principal_id="user-1")
        publishers = [
            _make_publisher("alpha.com"),
            _make_publisher("beta.com"),
            _make_publisher("gamma.com"),
        ]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"] as mock_get_audit,
            patches["log_activity"],
        ):
            mock_audit_instance = MagicMock()
            mock_get_audit.return_value = mock_audit_instance

            _list_authorized_properties_impl(req=None, identity=identity)

            mock_audit_instance.log_operation.assert_called_once()
            _, kwargs = mock_audit_instance.log_operation.call_args
            assert kwargs["operation"] == "list_authorized_properties"
            assert kwargs["success"] is True
            assert kwargs["details"]["publisher_count"] == 3
            assert sorted(kwargs["details"]["publisher_domains"]) == ["alpha.com", "beta.com", "gamma.com"]
            assert kwargs["principal_name"] == "user-1"

    def test_audit_uses_anonymous_when_no_principal(self):
        """M5: When principal_id is None, audit uses 'anonymous'."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant()
        identity = _make_identity(tenant, principal_id=None)
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"] as mock_get_audit,
            patches["log_activity"],
        ):
            mock_audit_instance = MagicMock()
            mock_get_audit.return_value = mock_audit_instance

            _list_authorized_properties_impl(req=None, identity=identity)

            _, kwargs = mock_audit_instance.log_operation.call_args
            assert kwargs["principal_name"] == "anonymous"
            assert kwargs["principal_id"] == "anonymous"


# ===========================================================================
# MEDIUM_RISK: M6 — Audit log on failure (scenario 10)
# ===========================================================================


class TestAuditLogFailure:
    """Database exception during listing logs audit with success=False."""

    def test_audit_called_on_failure(self):
        """M6: audit_logger.log_operation called with success=False and error string."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant()
        identity = _make_identity(tenant)

        patches = _patch_impl_dependencies(
            tenant=tenant,
            db_side_effect=RuntimeError("disk full"),
        )
        with (
            patches["db"],
            patches["audit"] as mock_get_audit,
            patches["log_activity"],
        ):
            mock_audit_instance = MagicMock()
            mock_get_audit.return_value = mock_audit_instance

            with pytest.raises(AdCPAdapterError, match="Failed to list authorized properties"):
                _list_authorized_properties_impl(req=None, identity=identity)

            mock_audit_instance.log_operation.assert_called_once()
            _, kwargs = mock_audit_instance.log_operation.call_args
            assert kwargs["success"] is False
            assert "disk full" in kwargs["error"]


# ===========================================================================
# MEDIUM_RISK: M7 — Advertising policy omitted when disabled (scenario 26)
# ===========================================================================


class TestAdvertisingPolicyOmittedWhenDisabled:
    """When tenant has advertising_policy.enabled=False, advertising_policies is absent."""

    def test_policy_disabled_explicitly(self):
        """M7: enabled=False => no advertising_policies in response."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant(
            advertising_policy={
                "enabled": False,
                "default_prohibited_categories": ["Alcohol"],
            }
        )
        identity = _make_identity(tenant)
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=None, identity=identity)

        assert result.advertising_policies is None
        data = result.model_dump()
        assert "advertising_policies" not in data

    def test_policy_missing_entirely(self):
        """M7: No advertising_policy key in tenant => no advertising_policies in response."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant()  # No advertising_policy key
        identity = _make_identity(tenant)
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=None, identity=identity)

        assert result.advertising_policies is None

    def test_policy_none_value(self):
        """M7: advertising_policy=None in tenant => no advertising_policies in response."""
        from src.core.tools.properties import _list_authorized_properties_impl

        tenant = _make_mock_tenant(advertising_policy=None)
        identity = _make_identity(tenant)
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["db"],
            patches["audit"],
            patches["log_activity"],
        ):
            result = _list_authorized_properties_impl(req=None, identity=identity)

        assert result.advertising_policies is None
