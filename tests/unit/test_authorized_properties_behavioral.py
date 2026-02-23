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
from fastmcp.exceptions import ToolError

from src.core.schemas import ListAuthorizedPropertiesRequest, ListAuthorizedPropertiesResponse
from src.core.testing_hooks import AdCPTestContext

# --- Helpers ---


def _make_mock_tenant(tenant_id="test-tenant", name="Test Tenant", advertising_policy=None):
    """Build a tenant dict matching the shape returned by get_principal_from_context."""
    tenant = {"tenant_id": tenant_id, "name": name}
    if advertising_policy is not None:
        tenant["advertising_policy"] = advertising_policy
    return tenant


def _make_publisher(domain):
    """Build a mock PublisherPartner row."""
    pub = MagicMock()
    pub.publisher_domain = domain
    return pub


def _patch_impl_dependencies(
    tenant,
    publishers=None,
    principal_id=None,
    db_side_effect=None,
):
    """Return a dict of patches for _list_authorized_properties_impl dependencies.

    Args:
        tenant: The tenant dict (or None to simulate TENANT_ERROR).
        publishers: List of mock PublisherPartner objects.
        principal_id: Principal ID returned by get_principal_from_context.
        db_side_effect: If set, get_db_session context manager body raises this.
    """
    patches = {
        "auth": patch(
            "src.core.tools.properties.get_principal_from_context",
            return_value=(principal_id, tenant),
        ),
        "set_tenant": patch("src.core.tools.properties.set_current_tenant"),
        "get_tenant": patch("src.core.tools.properties.get_current_tenant", return_value=tenant),
        "audit": patch("src.core.tools.properties.get_audit_logger"),
        "testing_ctx": patch(
            "src.core.tools.properties.get_testing_context",
            return_value=AdCPTestContext(),
        ),
    }

    # Build mock DB session
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    if db_side_effect:
        # Make the scalars call raise
        mock_session.scalars.side_effect = db_side_effect
    else:
        mock_session.scalars.return_value.all.return_value = publishers or []

    patches["db"] = patch(
        "src.core.tools.properties.get_db_session",
        return_value=mock_session,
    )

    return patches


# ===========================================================================
# HIGH_RISK: H1 — TENANT_ERROR path (scenarios 6, 7)
# ===========================================================================


class TestTenantErrorPath:
    """When get_principal_from_context returns (None, None), ToolError('TENANT_ERROR') is raised."""

    def test_tenant_error_when_no_tenant_resolvable(self):
        """H1: No tenant from context and no current tenant raises TENANT_ERROR."""
        from src.core.tools.properties import _list_authorized_properties_impl

        ctx = MagicMock()

        with (
            patch(
                "src.core.tools.properties.get_principal_from_context",
                return_value=(None, None),
            ),
            patch("src.core.tools.properties.set_current_tenant"),
            patch("src.core.tools.properties.get_current_tenant", return_value=None),
        ):
            with pytest.raises(ToolError, match="TENANT_ERROR"):
                _list_authorized_properties_impl(req=None, context=ctx)

    def test_tenant_error_message_is_descriptive(self):
        """H1: TENANT_ERROR message mentions subdomain, virtual host, or header."""
        from src.core.tools.properties import _list_authorized_properties_impl

        ctx = MagicMock()

        with (
            patch(
                "src.core.tools.properties.get_principal_from_context",
                return_value=(None, None),
            ),
            patch("src.core.tools.properties.set_current_tenant"),
            patch("src.core.tools.properties.get_current_tenant", return_value=None),
        ):
            with pytest.raises(ToolError, match="subdomain|virtual host|x-adcp-tenant"):
                _list_authorized_properties_impl(req=None, context=ctx)


# ===========================================================================
# HIGH_RISK: H2 — PROPERTIES_ERROR path (scenarios 8, 9)
# ===========================================================================


class TestPropertiesErrorPath:
    """When the database query raises an exception, ToolError('PROPERTIES_ERROR') is raised."""

    def test_properties_error_on_db_exception(self):
        """H2: Database exception in _impl raises PROPERTIES_ERROR."""
        from src.core.tools.properties import _list_authorized_properties_impl

        ctx = MagicMock()
        tenant = _make_mock_tenant()

        patches = _patch_impl_dependencies(
            tenant=tenant,
            db_side_effect=RuntimeError("connection lost"),
        )
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            with pytest.raises(ToolError, match="PROPERTIES_ERROR"):
                _list_authorized_properties_impl(req=None, context=ctx)

    def test_properties_error_calls_audit_with_failure(self):
        """H2: PROPERTIES_ERROR path logs audit with success=False."""
        from src.core.tools.properties import _list_authorized_properties_impl

        ctx = MagicMock()
        tenant = _make_mock_tenant()

        patches = _patch_impl_dependencies(
            tenant=tenant,
            db_side_effect=RuntimeError("connection lost"),
        )
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"] as mock_get_audit,
            patches["testing_ctx"],
        ):
            mock_audit_instance = MagicMock()
            mock_get_audit.return_value = mock_audit_instance

            with pytest.raises(ToolError, match="PROPERTIES_ERROR"):
                _list_authorized_properties_impl(req=None, context=ctx)

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

        ctx = MagicMock()
        tenant = self._make_full_policy_tenant()
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=None, context=ctx)

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

        ctx = MagicMock()
        tenant = self._make_full_policy_tenant()
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=None, context=ctx)

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

        ctx = MagicMock()
        tenant = _make_mock_tenant(
            advertising_policy={
                "enabled": True,
                "default_prohibited_categories": ["Alcohol"],
            }
        )
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=None, context=ctx)

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

        ctx = MagicMock()
        tenant = _make_mock_tenant(
            advertising_policy={
                "enabled": True,
                "prohibited_advertisers": ["bad-actor.com"],
            }
        )
        publishers = [_make_publisher("news.example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=None, context=ctx)

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

        ctx = MagicMock()
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
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=None, context=ctx)

        # advertising_policies should not be set (None or absent from dump)
        assert result.advertising_policies is None
        data = result.model_dump()
        assert "advertising_policies" not in data

    def test_enabled_true_with_missing_keys_no_policy(self):
        """H5 variant: enabled=True but no policy keys present at all."""
        from src.core.tools.properties import _list_authorized_properties_impl

        ctx = MagicMock()
        tenant = _make_mock_tenant(
            advertising_policy={
                "enabled": True,
            }
        )
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=None, context=ctx)

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

        ctx = MagicMock()
        tenant = _make_mock_tenant(
            advertising_policy={
                "enabled": True,
                policy_key: policy_value,
            }
        )
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=None, context=ctx)

        policy = result.advertising_policies
        assert policy is not None
        assert "**Policy Enforcement:**" in policy


# ===========================================================================
# HIGH_RISK: H7 — MCP wrapper header extraction (scenario 2)
# ===========================================================================


class TestMCPWrapperHeaderExtraction:
    """The MCP wrapper list_authorized_properties extracts headers from FastMCP Context."""

    _stub_response = ListAuthorizedPropertiesResponse(publisher_domains=["stub.com"])

    def test_creates_minimal_context_from_request_headers(self):
        """H7: MCP wrapper creates MinimalContext with headers from request.

        When ctx is a FastMCP Context with request_context.request.headers,
        the wrapper builds a MinimalContext with those headers and passes it to _impl.
        """
        from fastmcp.server.context import Context

        from src.core.tools.properties import list_authorized_properties

        # Build a mock FastMCP Context that passes isinstance(ctx, Context)
        mock_ctx = MagicMock(spec=Context)
        mock_request = MagicMock()
        mock_request.headers = {
            "host": "tenant1.example.com",
            "x-adcp-auth": "token-abc",
            "apx-incoming-host": "tenant1.example.com",
        }
        mock_ctx.request_context = MagicMock()
        mock_ctx.request_context.request = mock_request

        with patch("src.core.tools.properties._list_authorized_properties_impl") as mock_impl:
            mock_impl.return_value = self._stub_response
            list_authorized_properties(req=None, ctx=mock_ctx)

            # Verify _impl was called with a context that has the extracted headers
            mock_impl.assert_called_once()
            passed_context = mock_impl.call_args[0][1]
            assert hasattr(passed_context, "headers")
            assert passed_context.headers["host"] == "tenant1.example.com"
            assert passed_context.headers["x-adcp-auth"] == "token-abc"
            assert passed_context.headers["apx-incoming-host"] == "tenant1.example.com"
            # MinimalContext also has meta.headers
            assert hasattr(passed_context, "meta")
            assert passed_context.meta["headers"] == passed_context.headers

    def test_falls_back_to_ctx_when_no_request_context(self):
        """H7: When ctx is a Context but request_context is None, falls back to ctx."""
        from fastmcp.server.context import Context

        from src.core.tools.properties import list_authorized_properties

        mock_ctx = MagicMock(spec=Context)
        mock_ctx.request_context = None  # No request context available

        with patch("src.core.tools.properties._list_authorized_properties_impl") as mock_impl:
            mock_impl.return_value = self._stub_response
            list_authorized_properties(req=None, ctx=mock_ctx)

            mock_impl.assert_called_once()
            passed_context = mock_impl.call_args[0][1]
            # Falls through to tool_context = ctx since request is None
            assert passed_context is mock_ctx

    def test_falls_back_to_ctx_when_not_context_instance(self):
        """H7: When ctx is not a Context instance, falls through without header extraction."""
        from src.core.tools.properties import list_authorized_properties

        # A plain MagicMock is truthy but not isinstance(ctx, Context)
        mock_ctx = MagicMock()

        with patch("src.core.tools.properties._list_authorized_properties_impl") as mock_impl:
            mock_impl.return_value = self._stub_response
            list_authorized_properties(req=None, ctx=mock_ctx)

            mock_impl.assert_called_once()
            # Because isinstance fails, request stays None, code falls to tool_context = ctx
            passed_context = mock_impl.call_args[0][1]
            assert passed_context is mock_ctx

    def test_falls_back_on_exception_during_header_extraction(self):
        """H7: When header extraction raises, wrapper catches and falls back to ctx."""
        from fastmcp.server.context import Context

        from src.core.tools.properties import list_authorized_properties

        mock_ctx = MagicMock(spec=Context)
        mock_ctx.request_context = MagicMock()
        # Make accessing request.headers raise an exception
        mock_request = MagicMock()
        type(mock_request).headers = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken")))
        mock_ctx.request_context.request = mock_request

        with patch("src.core.tools.properties._list_authorized_properties_impl") as mock_impl:
            mock_impl.return_value = self._stub_response
            list_authorized_properties(req=None, ctx=mock_ctx)

            mock_impl.assert_called_once()
            # On exception in the try block, falls back to tool_context = ctx
            passed_context = mock_impl.call_args[0][1]
            assert passed_context is mock_ctx

    def test_no_context_provided(self):
        """H7: MCP wrapper handles ctx=None gracefully."""
        from src.core.tools.properties import list_authorized_properties

        with patch("src.core.tools.properties._list_authorized_properties_impl") as mock_impl:
            mock_impl.return_value = self._stub_response
            list_authorized_properties(req=None, ctx=None)

            mock_impl.assert_called_once()
            passed_context = mock_impl.call_args[0][1]
            assert passed_context is None


# ===========================================================================
# MEDIUM_RISK: M1 — Context echo with value (scenario 21)
# ===========================================================================


class TestContextEchoWithValue:
    """When req.context has a value, it is echoed in the response."""

    def test_context_echoed_with_publishers(self):
        """M1: Context from request appears in response with non-empty portfolio."""
        from src.core.tools.properties import _list_authorized_properties_impl

        ctx = MagicMock()
        tenant = _make_mock_tenant()
        publishers = [_make_publisher("example.com"), _make_publisher("news.com")]

        req_context = ContextObject(campaign_id="abc-123", session="sess-1")
        req = ListAuthorizedPropertiesRequest(context=req_context)

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=req, context=ctx)

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

        ctx = MagicMock()
        tenant = _make_mock_tenant()

        req_context = ContextObject(session="xyz")
        req = ListAuthorizedPropertiesRequest(context=req_context)

        patches = _patch_impl_dependencies(tenant=tenant, publishers=[])
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=req, context=ctx)

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

        ctx = MagicMock()
        tenant = _make_mock_tenant()
        publishers = [_make_publisher("example.com")]

        req = ListAuthorizedPropertiesRequest()  # context defaults to None

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=req, context=ctx)

        assert result.context is None
        data = result.model_dump()
        assert "context" not in data

    def test_none_req_produces_none_context(self):
        """M3: req=None => response.context is None."""
        from src.core.tools.properties import _list_authorized_properties_impl

        ctx = MagicMock()
        tenant = _make_mock_tenant()
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=None, context=ctx)

        assert result.context is None


# ===========================================================================
# MEDIUM_RISK: M4 — Context echo complex nested (scenario 24)
# ===========================================================================


class TestContextEchoComplexNested:
    """Complex nested context is preserved exactly."""

    def test_deeply_nested_context_preserved(self):
        """M4: Nested dict with lists is echoed faithfully."""
        from src.core.tools.properties import _list_authorized_properties_impl

        ctx = MagicMock()
        tenant = _make_mock_tenant()
        publishers = [_make_publisher("example.com")]

        nested_context = ContextObject(
            campaign_id="camp-1",
            metadata={"tags": ["premium", "video"], "geo": {"country": "US", "regions": ["NY", "CA"]}},
            items=[1, 2, 3],
        )
        req = ListAuthorizedPropertiesRequest(context=nested_context)

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=req, context=ctx)

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

        ctx = MagicMock()
        tenant = _make_mock_tenant()
        publishers = [
            _make_publisher("alpha.com"),
            _make_publisher("beta.com"),
            _make_publisher("gamma.com"),
        ]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers, principal_id="user-1")
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"] as mock_get_audit,
            patches["testing_ctx"],
        ):
            mock_audit_instance = MagicMock()
            mock_get_audit.return_value = mock_audit_instance

            _list_authorized_properties_impl(req=None, context=ctx)

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

        ctx = MagicMock()
        tenant = _make_mock_tenant()
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers, principal_id=None)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"] as mock_get_audit,
            patches["testing_ctx"],
        ):
            mock_audit_instance = MagicMock()
            mock_get_audit.return_value = mock_audit_instance

            _list_authorized_properties_impl(req=None, context=ctx)

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

        ctx = MagicMock()
        tenant = _make_mock_tenant()

        patches = _patch_impl_dependencies(
            tenant=tenant,
            db_side_effect=RuntimeError("disk full"),
        )
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"] as mock_get_audit,
            patches["testing_ctx"],
        ):
            mock_audit_instance = MagicMock()
            mock_get_audit.return_value = mock_audit_instance

            with pytest.raises(ToolError, match="PROPERTIES_ERROR"):
                _list_authorized_properties_impl(req=None, context=ctx)

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

        ctx = MagicMock()
        tenant = _make_mock_tenant(
            advertising_policy={
                "enabled": False,
                "default_prohibited_categories": ["Alcohol"],
            }
        )
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=None, context=ctx)

        assert result.advertising_policies is None
        data = result.model_dump()
        assert "advertising_policies" not in data

    def test_policy_missing_entirely(self):
        """M7: No advertising_policy key in tenant => no advertising_policies in response."""
        from src.core.tools.properties import _list_authorized_properties_impl

        ctx = MagicMock()
        tenant = _make_mock_tenant()  # No advertising_policy key
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=None, context=ctx)

        assert result.advertising_policies is None

    def test_policy_none_value(self):
        """M7: advertising_policy=None in tenant => no advertising_policies in response."""
        from src.core.tools.properties import _list_authorized_properties_impl

        ctx = MagicMock()
        tenant = _make_mock_tenant(advertising_policy=None)
        publishers = [_make_publisher("example.com")]

        patches = _patch_impl_dependencies(tenant=tenant, publishers=publishers)
        with (
            patches["auth"],
            patches["set_tenant"],
            patches["get_tenant"],
            patches["db"],
            patches["audit"],
            patches["testing_ctx"],
        ):
            result = _list_authorized_properties_impl(req=None, context=ctx)

        assert result.advertising_policies is None
