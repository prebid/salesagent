"""Guard: MCP/A2A wrappers must pass all _impl function parameters.

Every parameter of an _impl function must be passed through by both its MCP
wrapper and A2A raw wrapper at call sites. Silently dropping parameters means
the transport boundary is incomplete — callers can't access functionality
that _impl provides.

Scanning approach: Hybrid — introspection for signatures, file-level AST
for call-site verification of which arguments are actually passed.

beads: salesagent-v0kb (structural-guard epic)
"""

import ast
import importlib
import inspect
from pathlib import Path

TOOLS_DIR = Path("src/core/tools")

# All _impl functions and their modules
IMPL_REGISTRY = [
    ("src.core.tools.capabilities", "_get_adcp_capabilities_impl"),
    ("src.core.tools.creative_formats", "_list_creative_formats_impl"),
    ("src.core.tools.properties", "_list_authorized_properties_impl"),
    ("src.core.tools.products", "_get_products_impl"),
    ("src.core.tools.media_buy_create", "_create_media_buy_impl"),
    ("src.core.tools.media_buy_update", "_update_media_buy_impl"),
    ("src.core.tools.media_buy_delivery", "_get_media_buy_delivery_impl"),
    ("src.core.tools.performance", "_update_performance_index_impl"),
    ("src.core.tools.creatives._sync", "_sync_creatives_impl"),
    ("src.core.tools.creatives.listing", "_list_creatives_impl"),
    ("src.core.tools.media_buy_list", "_get_media_buys_impl"),
    ("src.core.tools.signals", "_get_signals_impl"),
    ("src.core.tools.signals", "_activate_signal_impl"),
]

# Known violations: (module_path, impl_name, wrapper_kind, missing_param)
# Each entry is a known parameter drop that needs fixing.
# Format: "module::impl_name::wrapper_kind::param_name"
KNOWN_VIOLATIONS = {
    # A2A wrapper for create_media_buy doesn't pass context_id
    "src.core.tools.media_buy_create::_create_media_buy_impl::a2a::context_id",
    # A2A wrapper for update_media_buy doesn't pass context_id
    "src.core.tools.media_buy_update::_update_media_buy_impl::a2a::context_id",
}

# Parameters resolved at the boundary, not forwarded from the caller
BOUNDARY_RESOLVED_PARAMS = {"identity"}


def _module_to_filepath(module_path: str) -> Path:
    """Convert dotted module path to filesystem path."""
    parts = module_path.replace(".", "/")
    path = Path(f"{parts}.py")
    if path.exists():
        return path
    # Try as package __init__
    pkg_path = Path(parts) / "__init__.py"
    if pkg_path.exists():
        return pkg_path
    return path


def _get_impl_params(module_path: str, func_name: str) -> list[str]:
    """Get parameter names for an _impl function (excluding boundary-resolved)."""
    mod = importlib.import_module(module_path)
    func = getattr(mod, func_name)
    sig = inspect.signature(func)
    return [name for name in sig.parameters if name not in BOUNDARY_RESOLVED_PARAMS]


def _find_wrapper_info(module_path: str, impl_name: str) -> dict:
    """Find MCP wrapper and A2A raw function for an _impl function.

    Returns {"mcp": (name, module_path), "a2a": (name, module_path)} or None entries.
    """
    base_name = impl_name.removeprefix("_").removesuffix("_impl")
    mcp_name = base_name
    a2a_name = f"{base_name}_raw"

    mod = importlib.import_module(module_path)
    result = {}

    result["mcp"] = (mcp_name, module_path) if hasattr(mod, mcp_name) else None
    result["a2a"] = (a2a_name, module_path) if hasattr(mod, a2a_name) else None

    # Check sibling modules for A2A wrapper
    if result["a2a"] is None:
        parent_module = module_path.rsplit(".", 1)[0]
        try:
            sibling = importlib.import_module(f"{parent_module}.sync_wrappers")
            if hasattr(sibling, a2a_name):
                result["a2a"] = (a2a_name, f"{parent_module}.sync_wrappers")
        except ImportError:
            pass

    return result


def _find_impl_call_args_in_function(file_path: Path, wrapper_name: str, impl_name: str) -> list[tuple[set[str], int]]:
    """Find calls to impl_name within wrapper_name in a file using AST.

    Returns list of (keyword_arg_names, positional_arg_count) tuples.
    """
    if not file_path.exists():
        return []

    source = file_path.read_text()
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []

    # Find the wrapper function node
    wrapper_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == wrapper_name:
                wrapper_node = node
                break

    if wrapper_node is None:
        return []

    # Find _impl calls within the wrapper function body
    results = []
    for node in ast.walk(wrapper_node):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        called_name = None
        if isinstance(func, ast.Name):
            called_name = func.id
        elif isinstance(func, ast.Attribute):
            called_name = func.attr

        if called_name != impl_name:
            continue

        kwargs = {kw.arg for kw in node.keywords if kw.arg is not None}
        n_positional = len(node.args)
        results.append((kwargs, n_positional))

    return results


def _check_wrapper_completeness(
    module_path: str, impl_name: str, wrapper_name: str, wrapper_module: str, wrapper_kind: str
) -> list[str]:
    """Check if a wrapper passes all _impl params. Returns list of violation descriptions."""
    impl_params = _get_impl_params(module_path, impl_name)
    file_path = _module_to_filepath(wrapper_module)
    call_arg_sets = _find_impl_call_args_in_function(file_path, wrapper_name, impl_name)

    if not call_arg_sets:
        return []

    violations = []
    for kwargs, n_positional in call_arg_sets:
        for i, param in enumerate(impl_params):
            if param in BOUNDARY_RESOLVED_PARAMS:
                continue
            key = f"{module_path}::{impl_name}::{wrapper_kind}::{param}"
            if param not in kwargs and i >= n_positional:
                if key in KNOWN_VIOLATIONS:
                    continue
                violations.append(
                    f"{wrapper_kind.upper()} wrapper '{wrapper_name}' in {wrapper_module} "
                    f"doesn't pass '{param}' to {impl_name}"
                )
    return violations


class TestBoundaryCompleteness:
    """MCP and A2A wrappers must pass all _impl parameters at call sites."""

    def test_mcp_wrappers_pass_all_impl_params(self):
        """Each MCP wrapper must pass all non-identity _impl parameters."""
        violations = []
        for module_path, impl_name in IMPL_REGISTRY:
            info = _find_wrapper_info(module_path, impl_name)
            if info["mcp"] is None:
                continue
            wrapper_name, wrapper_module = info["mcp"]
            violations.extend(_check_wrapper_completeness(module_path, impl_name, wrapper_name, wrapper_module, "mcp"))

        assert not violations, "MCP wrappers dropping _impl parameters:\n" + "\n".join(f"  - {v}" for v in violations)

    def test_a2a_wrappers_pass_all_impl_params(self):
        """Each A2A raw wrapper must pass all non-identity _impl parameters."""
        violations = []
        for module_path, impl_name in IMPL_REGISTRY:
            info = _find_wrapper_info(module_path, impl_name)
            if info["a2a"] is None:
                continue
            wrapper_name, wrapper_module = info["a2a"]
            violations.extend(_check_wrapper_completeness(module_path, impl_name, wrapper_name, wrapper_module, "a2a"))

        assert not violations, "A2A wrappers dropping _impl parameters:\n" + "\n".join(f"  - {v}" for v in violations)

    def test_known_violations_are_still_violations(self):
        """Known violations in the allowlist must still be actual violations.

        If a known violation gets fixed, it should be removed from the allowlist.
        This prevents the allowlist from becoming stale.
        """
        still_violated = set()

        for violation_key in KNOWN_VIOLATIONS:
            parts = violation_key.split("::")
            if len(parts) != 4:
                continue
            module_path, impl_name, wrapper_kind, param = parts

            impl_params = _get_impl_params(module_path, impl_name)
            if param not in impl_params:
                continue

            info = _find_wrapper_info(module_path, impl_name)
            wrapper_entry = info.get(wrapper_kind)
            if wrapper_entry is None:
                continue
            wrapper_name, wrapper_module = wrapper_entry

            file_path = _module_to_filepath(wrapper_module)
            call_arg_sets = _find_impl_call_args_in_function(file_path, wrapper_name, impl_name)
            for kwargs, n_positional in call_arg_sets:
                param_idx = impl_params.index(param) if param in impl_params else -1
                if param not in kwargs and (param_idx < 0 or param_idx >= n_positional):
                    still_violated.add(violation_key)

        fixed = KNOWN_VIOLATIONS - still_violated
        if fixed:
            msg = "These known violations have been FIXED — remove from KNOWN_VIOLATIONS:\n" + "\n".join(
                f"  - {v}" for v in sorted(fixed)
            )
            raise AssertionError(msg)
