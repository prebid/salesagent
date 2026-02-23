"""Behavioral snapshot tests for update_performance_index (UC-009).

These tests encode the CURRENT behavior of the update_performance_index tool,
serving as regression guardrails during the FastAPI migration. Tests are ordered
by migration risk: HIGH_RISK first.

Covers BDD scenarios from BR-UC-009-update-performance-index.feature that are
ACCURATE (match current code behavior). Aspirational BDD assertions that
contradict current code are deferred to protocol compliance work.

Reference: beads salesagent-j6uf
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from adcp.types.generated_poc.core.context import ContextObject
from fastmcp.exceptions import ToolError

from src.core.schemas import PackagePerformance
from src.core.tool_context import ToolContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_context(
    principal_id: str = "principal_1",
    tenant_id: str = "tenant_1",
) -> ToolContext:
    """Build a minimal ToolContext for testing."""
    return ToolContext(
        context_id="ctx_test",
        tenant_id=tenant_id,
        principal_id=principal_id,
        tool_name="update_performance_index",
        request_timestamp=datetime.now(UTC),
    )


def _patch_happy_path(
    adapter_return: bool = True,
    principal_id: str = "principal_1",
):
    """Return a stack of patches for the common happy-path mocks.

    Patches (in order):
        1. _verify_principal  - noop
        2. get_principal_id_from_context  - returns principal_id
        3. get_principal_object  - returns a mock principal
        4. get_adapter  - returns a mock adapter whose
           update_media_buy_performance_index returns *adapter_return*
        5. get_current_tenant  - returns a minimal tenant dict
        6. get_audit_logger  - returns a mock audit logger

    Returns a context-manager-like stack *and* exposes the mock adapter and
    audit logger for assertions.
    """
    from contextlib import ExitStack

    stack = ExitStack()
    mocks: dict = {}

    mock_adapter = MagicMock()
    mock_adapter.update_media_buy_performance_index.return_value = adapter_return
    mocks["adapter"] = mock_adapter

    mock_audit_logger = MagicMock()
    mocks["audit_logger"] = mock_audit_logger

    mock_principal = MagicMock()
    mock_principal.principal_id = principal_id
    mocks["principal"] = mock_principal

    stack.enter_context(
        patch(
            "src.core.tools.performance._verify_principal",
            return_value=None,
        )
    )
    stack.enter_context(
        patch(
            "src.core.tools.performance._get_principal_id_from_context",
            return_value=principal_id,
        )
    )
    stack.enter_context(
        patch(
            "src.core.tools.performance.get_principal_object",
            return_value=mock_principal,
        )
    )
    stack.enter_context(
        patch(
            "src.core.tools.performance.get_adapter",
            return_value=mock_adapter,
        )
    )
    stack.enter_context(
        patch(
            "src.core.tools.performance.get_current_tenant",
            return_value={"tenant_id": "tenant_1"},
        )
    )
    stack.enter_context(
        patch(
            "src.core.tools.performance.get_audit_logger",
            return_value=mock_audit_logger,
        )
    )

    return stack, mocks


# ===========================================================================
# HIGH_RISK tests (H1-H7)
# ===========================================================================


class TestHighRiskMCP:
    """HIGH_RISK behavioral tests for the MCP path."""

    # H1 ---------------------------------------------------------------
    def test_happy_path_mcp_success_response(self):
        """H1: Happy-path success response has correct status, detail, and context.

        Covers: #1 T-UC-009-main-mcp
        """
        from src.core.tools.performance import _update_performance_index_impl

        ctx = _make_tool_context()
        stack, _mocks = _patch_happy_path()

        with stack:
            response = _update_performance_index_impl(
                media_buy_id="mb_1",
                performance_data=[{"product_id": "p1", "performance_index": 1.2}],
                context=ContextObject(session_id="s1"),
                ctx=ctx,
            )

        assert response.status == "success"
        assert response.detail == "Performance index updated for 1 products"
        assert response.context is not None
        assert response.context.session_id == "s1"

    # H2 ---------------------------------------------------------------
    def test_product_to_package_mapping(self):
        """H2: product_id in performance_data maps to package_id on adapter call.

        Covers: #2 T-UC-009-main-mcp-adapter
        """
        from src.core.tools.performance import _update_performance_index_impl

        ctx = _make_tool_context()
        stack, mocks = _patch_happy_path()

        with stack:
            _update_performance_index_impl(
                media_buy_id="mb_1",
                performance_data=[{"product_id": "prod_abc", "performance_index": 0.9}],
                ctx=ctx,
            )

        adapter = mocks["adapter"]
        adapter.update_media_buy_performance_index.assert_called_once()
        call_args = adapter.update_media_buy_performance_index.call_args
        packages = call_args[0][1]  # second positional arg

        assert len(packages) == 1
        pkg = packages[0]
        assert isinstance(pkg, PackagePerformance)
        assert pkg.package_id == "prod_abc"
        assert pkg.performance_index == 0.9

    # H3 ---------------------------------------------------------------
    def test_batch_multiple_products(self):
        """H3: Batch with 3 products passes all to adapter, audit avg ~ 0.833.

        Covers: #6 T-UC-009-batch
        """
        from src.core.tools.performance import _update_performance_index_impl

        ctx = _make_tool_context()
        stack, mocks = _patch_happy_path()

        perf_data = [
            {"product_id": "p1", "performance_index": 1.2},
            {"product_id": "p2", "performance_index": 0.5},
            {"product_id": "p3", "performance_index": 0.8},
        ]

        with stack:
            response = _update_performance_index_impl(
                media_buy_id="mb_1",
                performance_data=perf_data,
                ctx=ctx,
            )

        # Adapter receives 3 PackagePerformance objects
        adapter = mocks["adapter"]
        call_args = adapter.update_media_buy_performance_index.call_args
        packages = call_args[0][1]
        assert len(packages) == 3

        # Audit logger records product_count and avg
        audit_logger = mocks["audit_logger"]
        audit_logger.log_operation.assert_called_once()
        audit_call = audit_logger.log_operation.call_args
        details = audit_call.kwargs.get("details") or audit_call[1].get("details")
        assert details["product_count"] == 3
        assert abs(details["avg_performance_index"] - 0.833) < 0.01

        # Response reflects batch
        assert response.status == "success"
        assert response.detail == "Performance index updated for 3 products"

    # H4 ---------------------------------------------------------------
    def test_context_echo_on_success(self):
        """H4: Context with session_id and trace_id is echoed on success.

        Covers: #15 T-UC-009-inv-043-1
        """
        from src.core.tools.performance import _update_performance_index_impl

        ctx = _make_tool_context()
        stack, _mocks = _patch_happy_path()

        with stack:
            response = _update_performance_index_impl(
                media_buy_id="mb_1",
                performance_data=[{"product_id": "p1", "performance_index": 1.0}],
                context=ContextObject(session_id="sess_1", trace_id="tr_1"),
                ctx=ctx,
            )

        assert response.context is not None
        assert response.context.session_id == "sess_1"
        assert response.context.trace_id == "tr_1"

    # H5 ---------------------------------------------------------------
    def test_media_buy_not_found_mcp(self):
        """H5: ValueError raised when media buy does not exist.

        Covers: #26 T-UC-009-ext-a-mcp
        """
        from src.core.tools.performance import _update_performance_index_impl

        ctx = _make_tool_context()

        with patch(
            "src.core.tools.performance._verify_principal",
            side_effect=ValueError("Media buy 'mb_999' not found."),
        ):
            with pytest.raises(ValueError, match="not found"):
                _update_performance_index_impl(
                    media_buy_id="mb_999",
                    performance_data=[{"product_id": "p1", "performance_index": 1.0}],
                    ctx=ctx,
                )

    # H6 ---------------------------------------------------------------
    def test_validation_error_missing_performance_index(self):
        """H6: ToolError raised when performance_data item missing performance_index.

        Covers: #28 T-UC-009-ext-b-mcp
        """
        from src.core.tools.performance import _update_performance_index_impl

        ctx = _make_tool_context()

        with pytest.raises(ToolError) as exc_info:
            _update_performance_index_impl(
                media_buy_id="mb_1",
                performance_data=[{"product_id": "p1"}],  # missing performance_index
                ctx=ctx,
            )

        assert "performance_index" in str(exc_info.value).lower()

    # H7 ---------------------------------------------------------------
    def test_adapter_returns_false_status_failed(self):
        """H7: When adapter returns False, response status is 'failed' with context echo.

        Covers: #37 T-UC-009-ext-d-false
        Note: detail message is the same string regardless of success/failure.
        """
        from src.core.tools.performance import _update_performance_index_impl

        ctx = _make_tool_context()
        stack, _mocks = _patch_happy_path(adapter_return=False)

        with stack:
            response = _update_performance_index_impl(
                media_buy_id="mb_1",
                performance_data=[{"product_id": "p1", "performance_index": 1.0}],
                context=ContextObject(session_id="sess_fail"),
                ctx=ctx,
            )

        assert response.status == "failed"
        # Detail message is the same regardless of success/failure (current behavior)
        assert response.detail == "Performance index updated for 1 products"
        # Context is echoed even on failure
        assert response.context is not None
        assert response.context.session_id == "sess_fail"


# ===========================================================================
# HIGH_RISK A2A-specific tests (H8-H9)
# ===========================================================================


class TestHighRiskA2A:
    """HIGH_RISK behavioral tests for the A2A path."""

    # H8 ---------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_a2a_validation_error_missing_params(self):
        """H8: A2A handler returns validation error dict when params are empty.

        Covers: #30 T-UC-009-ext-b-rest
        The handler calls _create_tool_context_from_a2a before parameter validation,
        so we must mock that to reach the Pydantic validation path.
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()
        mock_tool_context = _make_tool_context()
        handler._create_tool_context_from_a2a = MagicMock(return_value=mock_tool_context)

        result = await handler._handle_update_performance_index_skill(
            parameters={},
            auth_token="test-token",
        )

        assert isinstance(result, dict)
        assert result["success"] is False
        assert "required_parameters" in result
        assert result["required_parameters"] == ["media_buy_id", "performance_data"]

    # H9 ---------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_a2a_happy_path_correct_params(self):
        """H9: A2A handler with correct params delegates to shared impl successfully.

        Covers: #4 T-UC-009-main-rest
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()
        mock_tool_context = _make_tool_context()

        # Mock _create_tool_context_from_a2a and _tool_context_to_mcp_context
        handler._create_tool_context_from_a2a = MagicMock(return_value=mock_tool_context)
        handler._tool_context_to_mcp_context = MagicMock(return_value=mock_tool_context)

        with patch(
            "src.a2a_server.adcp_a2a_server.core_update_performance_index_tool",
        ) as mock_core_tool:
            # The A2A handler calls update_performance_index_raw which returns the response
            from src.core.schemas import UpdatePerformanceIndexResponse

            mock_response = UpdatePerformanceIndexResponse(
                status="success",
                detail="Performance index updated for 1 products",
            )
            mock_core_tool.return_value = mock_response

            result = await handler._handle_update_performance_index_skill(
                parameters={
                    "media_buy_id": "mb_test_123",
                    "performance_data": [{"product_id": "p1", "performance_index": 1.25}],
                },
                auth_token="test-token",
            )

        # Handler delegates to core tool and returns its result
        mock_core_tool.assert_called_once()
        # Result should be the response object (not just routing metadata)
        assert result is mock_response
