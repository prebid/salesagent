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


def _extract_call_kwargs(file_path: Path, caller_name: str, callee_name: str) -> set[str]:
    """Extract keyword arguments passed from caller to callee function.

    Finds calls like `callee_name(foo=foo, bar=bar, ...)` inside the named
    caller function and returns the set of keyword argument names.
    """
    source = file_path.read_text()
    tree = ast.parse(source, filename=str(file_path))

    caller_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == caller_name:
                caller_node = node
                break

    if caller_node is None:
        return set()

    kwargs = set()
    for node in ast.walk(caller_node):
        if not isinstance(node, ast.Call):
            continue
        called_name = None
        if isinstance(node.func, ast.Name):
            called_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            called_name = node.func.attr
        if called_name != callee_name:
            continue
        for kw in node.keywords:
            if kw.arg is not None:
                kwargs.add(kw.arg)

    return kwargs


# ---------------------------------------------------------------------------
# Tests — create_media_buy
# ---------------------------------------------------------------------------

CREATE_FILE = Path("src/core/tools/media_buy_create.py")

# AdCP spec fields that MUST be forwarded from wrappers into CreateMediaBuyRequest
# AdCP spec fields that MUST be forwarded from wrappers into CreateMediaBuyRequest
# buyer_ref and buyer_campaign_ref removed in adcp 3.12
CREATE_SPEC_FIELDS = {"brand", "packages", "start_time", "end_time", "po_number", "reporting_webhook", "context", "ext"}


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
# buyer_ref removed in adcp 3.12
UPDATE_SPEC_FIELDS = {
    "media_buy_id",
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
    """MCP and A2A update wrappers must forward all AdCP fields through _build_update_request."""

    def test_mcp_wrapper_accepts_all_spec_fields(self):
        """MCP update_media_buy must accept all AdCP spec fields as parameters."""
        params = _extract_wrapper_params(UPDATE_FILE, "update_media_buy")
        missing = UPDATE_SPEC_FIELDS - params
        assert not missing, (
            f"MCP wrapper 'update_media_buy' doesn't accept AdCP fields as parameters: {sorted(missing)}"
        )

    def test_a2a_wrapper_accepts_all_spec_fields(self):
        """A2A update_media_buy_raw must accept all AdCP spec fields as parameters."""
        params = _extract_wrapper_params(UPDATE_FILE, "update_media_buy_raw")
        missing = UPDATE_SPEC_FIELDS - params
        assert not missing, (
            f"A2A wrapper 'update_media_buy_raw' doesn't accept AdCP fields as parameters: {sorted(missing)}"
        )

    def test_build_update_request_accepts_all_spec_fields(self):
        """_build_update_request must accept all AdCP spec fields as parameters."""
        params = _extract_wrapper_params(UPDATE_FILE, "_build_update_request")
        missing = UPDATE_SPEC_FIELDS - params
        assert not missing, f"_build_update_request doesn't accept AdCP fields as parameters: {sorted(missing)}"

    def test_mcp_wrapper_forwards_all_spec_fields_to_build(self):
        """MCP wrapper must pass all spec fields to _build_update_request call site."""
        kwargs = _extract_call_kwargs(UPDATE_FILE, "update_media_buy", "_build_update_request")
        missing = UPDATE_SPEC_FIELDS - kwargs
        assert not missing, (
            f"MCP wrapper 'update_media_buy' doesn't forward AdCP fields to _build_update_request: {sorted(missing)}"
        )

    def test_a2a_wrapper_forwards_all_spec_fields_to_build(self):
        """A2A wrapper must pass all spec fields to _build_update_request call site."""
        kwargs = _extract_call_kwargs(UPDATE_FILE, "update_media_buy_raw", "_build_update_request")
        missing = UPDATE_SPEC_FIELDS - kwargs
        assert not missing, (
            f"A2A wrapper 'update_media_buy_raw' doesn't forward AdCP fields to "
            f"_build_update_request: {sorted(missing)}"
        )

    def test_build_update_request_constructs_with_all_spec_fields(self):
        """_build_update_request must include all spec fields in UpdateMediaBuyRequest construction."""
        # _build_update_request uses request_params dict, not direct constructor kwargs.
        # Check that every spec field has a `request_params["field"] = field` assignment.
        source = Path(UPDATE_FILE).read_text()
        tree = ast.parse(source)

        # Find _build_update_request
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "_build_update_request":
                    func_node = node
                    break

        assert func_node is not None, "_build_update_request function not found"

        # Find all request_params["key"] = ... assignments
        assigned_keys = set()
        for node in ast.walk(func_node):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "request_params"
                        and isinstance(target.slice, ast.Constant)
                        and isinstance(target.slice.value, str)
                    ):
                        assigned_keys.add(target.slice.value)

        missing = UPDATE_SPEC_FIELDS - assigned_keys
        assert not missing, f"_build_update_request doesn't include AdCP fields in request_params: {sorted(missing)}"
