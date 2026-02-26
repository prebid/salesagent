"""Regression test: transport wrappers must forward all AdCP request fields to _impl.

Bug salesagent-7gnv: MCP and A2A wrappers for create_media_buy and update_media_buy
silently dropped buyer_campaign_ref and ext before constructing the request object.
These fields are part of the AdCP spec and must reach _impl via the request object.

Core invariant: Every AdCP-spec field accepted by the wrapper must be included in
the request object passed to _impl. No silent field drops at the transport boundary.
"""

import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_request_constructor_kwargs(file_path: Path, wrapper_name: str, request_class: str) -> set[str]:
    """Extract keyword arguments passed to a request constructor within a wrapper function.

    Finds calls like `CreateMediaBuyRequest(buyer_ref=..., brand=..., ...)` inside
    the named wrapper function and returns the set of keyword argument names.
    """
    source = file_path.read_text()
    tree = ast.parse(source, filename=str(file_path))

    # Find the wrapper function
    wrapper_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == wrapper_name:
                wrapper_node = node
                break

    if wrapper_node is None:
        return set()

    # Find request constructor calls within the wrapper
    kwargs = set()
    for node in ast.walk(wrapper_node):
        if not isinstance(node, ast.Call):
            continue
        called_name = None
        if isinstance(node.func, ast.Name):
            called_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            called_name = node.func.attr
        if called_name != request_class:
            continue
        for kw in node.keywords:
            if kw.arg is not None:
                kwargs.add(kw.arg)

    return kwargs


def _extract_wrapper_params(file_path: Path, wrapper_name: str) -> set[str]:
    """Extract parameter names from a wrapper function signature."""
    source = file_path.read_text()
    tree = ast.parse(source, filename=str(file_path))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == wrapper_name:
                return {arg.arg for arg in node.args.args}
    return set()


# Fields that exist in the wrapper signature for legacy/internal reasons
# but are NOT part of the AdCP request schema — they're handled separately
WRAPPER_ONLY_PARAMS = {
    "create_media_buy": {
        "ctx",  # FastMCP context
        "product_ids",  # Legacy format conversion
        "start_date",  # Legacy format conversion
        "end_date",  # Legacy format conversion
        "total_budget",  # Legacy format conversion
        "budget",  # Deprecated — package-level only
        "targeting_overlay",  # Processed separately
        "pacing",  # Processed separately
        "daily_budget",  # Processed separately
        "creatives",  # Processed separately
        "required_axe_signals",  # Processed separately
        "enable_creative_macro",  # Processed separately
        "strategy_id",  # Processed separately
        "push_notification_config",  # Separate _impl param
        "webhook_url",  # Legacy
    },
    "create_media_buy_raw": {
        "ctx",  # FastMCP context
        "identity",  # Boundary-resolved
        "product_ids",  # Legacy format conversion
        "start_date",  # Legacy format conversion
        "end_date",  # Legacy format conversion
        "total_budget",  # Legacy format conversion
        "budget",  # Deprecated — package-level only
        "targeting_overlay",  # Processed separately
        "pacing",  # Processed separately
        "daily_budget",  # Processed separately
        "creatives",  # Processed separately
        "required_axe_signals",  # Processed separately
        "enable_creative_macro",  # Processed separately
        "strategy_id",  # Processed separately
        "push_notification_config",  # Separate _impl param
    },
}


# ---------------------------------------------------------------------------
# Tests — create_media_buy
# ---------------------------------------------------------------------------

CREATE_FILE = Path("src/core/tools/media_buy_create.py")

# AdCP spec fields that MUST be forwarded from wrappers into CreateMediaBuyRequest
CREATE_SPEC_FIELDS = {
    "buyer_ref",
    "brand",
    "packages",
    "start_time",
    "end_time",
    "po_number",
    "reporting_webhook",
    "context",
    "buyer_campaign_ref",
    "ext",
}


class TestCreateMediaBuyFieldForwarding:
    """MCP and A2A wrappers must forward all AdCP fields into CreateMediaBuyRequest."""

    def test_mcp_wrapper_constructs_request_with_all_spec_fields(self):
        """MCP create_media_buy must pass all AdCP spec fields to CreateMediaBuyRequest."""
        kwargs = _extract_request_constructor_kwargs(CREATE_FILE, "create_media_buy", "CreateMediaBuyRequest")
        missing = CREATE_SPEC_FIELDS - kwargs
        assert not missing, (
            f"MCP wrapper 'create_media_buy' drops AdCP fields when constructing "
            f"CreateMediaBuyRequest: {sorted(missing)}"
        )

    def test_a2a_wrapper_constructs_request_with_all_spec_fields(self):
        """A2A create_media_buy_raw must pass all AdCP spec fields to CreateMediaBuyRequest."""
        kwargs = _extract_request_constructor_kwargs(CREATE_FILE, "create_media_buy_raw", "CreateMediaBuyRequest")
        missing = CREATE_SPEC_FIELDS - kwargs
        assert not missing, (
            f"A2A wrapper 'create_media_buy_raw' drops AdCP fields when constructing "
            f"CreateMediaBuyRequest: {sorted(missing)}"
        )

    def test_mcp_wrapper_accepts_all_spec_fields_as_params(self):
        """MCP create_media_buy must accept all AdCP spec fields as parameters."""
        params = _extract_wrapper_params(CREATE_FILE, "create_media_buy")
        missing = CREATE_SPEC_FIELDS - params
        assert not missing, (
            f"MCP wrapper 'create_media_buy' doesn't accept AdCP fields as parameters: {sorted(missing)}"
        )

    def test_a2a_wrapper_accepts_all_spec_fields_as_params(self):
        """A2A create_media_buy_raw must accept all AdCP spec fields as parameters."""
        params = _extract_wrapper_params(CREATE_FILE, "create_media_buy_raw")
        missing = CREATE_SPEC_FIELDS - params
        assert not missing, (
            f"A2A wrapper 'create_media_buy_raw' doesn't accept AdCP fields as parameters: {sorted(missing)}"
        )


# ---------------------------------------------------------------------------
# Tests — update_media_buy
# ---------------------------------------------------------------------------

UPDATE_FILE = Path("src/core/tools/media_buy_update.py")

# AdCP spec fields that must reach the UpdateMediaBuyRequest via _build_update_request
UPDATE_SPEC_FIELDS = {
    "media_buy_id",
    "buyer_ref",
    "paused",
    "start_time",
    "end_time",
    "packages",
    "push_notification_config",
    "context",
    "reporting_webhook",
    "ext",
}


class TestUpdateMediaBuyFieldForwarding:
    """MCP and A2A update wrappers must forward all AdCP fields into _build_update_request."""

    def test_mcp_wrapper_accepts_ext(self):
        """MCP update_media_buy must accept ext as a parameter."""
        params = _extract_wrapper_params(UPDATE_FILE, "update_media_buy")
        assert "ext" in params, "MCP wrapper 'update_media_buy' doesn't accept 'ext' as a parameter"

    def test_a2a_wrapper_accepts_ext(self):
        """A2A update_media_buy_raw must accept ext as a parameter."""
        params = _extract_wrapper_params(UPDATE_FILE, "update_media_buy_raw")
        assert "ext" in params, "A2A wrapper 'update_media_buy_raw' doesn't accept 'ext' as a parameter"

    def test_build_update_request_accepts_ext(self):
        """_build_update_request must accept and forward ext."""
        params = _extract_wrapper_params(UPDATE_FILE, "_build_update_request")
        assert "ext" in params, "_build_update_request doesn't accept 'ext' as a parameter"
