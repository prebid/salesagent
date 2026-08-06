"""Structural guard for the MCP null-field-leak disease (salesagent-oyiv.7 /
salesagent-oyiv.16).

FastMCP serializes MCP ToolResult.structured_content via
pydantic_core.to_jsonable_python(), which bypasses every Python-method
model_dump() override in this codebase. Only a response class carrying
NestedModelSerializerMixin (a real @model_serializer(mode="wrap") hook) gets
its unset optional fields filtered on that path — anything else leaks null
top-level fields on the wire.

This guard pins the disease pattern going forward: every
ToolResult(structured_content=...) call site in src/core/tools/ must resolve
to response class(es) that either carry the mixin or are on the small,
reasoned ALLOWLIST below. The non-vacuity count assertion means a brand-new
call site fails loud (forcing a deliberate MIGRATE/ALLOWLIST decision) rather
than silently inheriting the bug.
"""

from __future__ import annotations

import ast
import importlib

from tests.unit._architecture_helpers import (
    assert_violations_match_allowlist,
    iter_call_expressions,
    repo_root,
    safe_parse,
)

_SCAN_DIR = repo_root() / "src" / "core" / "tools"

# (repo-relative file, enclosing function name) -> dotted class path(es).
# Most sites return a single concrete class; some _impl functions have a Union
# return type (e.g. update_media_buy's UpdateMediaBuyResult | UpdateMediaBuySubmitted)
# — list every class that can actually reach ToolResult(structured_content=...)
# at that site, not just the first one. Built from an exhaustive scan (see
# test_meta_scan_matches_known_site_count below); a new site not in this table
# is a NEW ToolResult(structured_content=) construction that needs a deliberate
# MIGRATE/ALLOWLIST decision, not a silent pass-through.
_TOOLRESULT_SITES: dict[tuple[str, str], tuple[str, ...]] = {
    ("src/core/tools/accounts.py", "list_accounts"): ("src.core.schemas.account.ListAccountsResponse",),
    ("src/core/tools/accounts.py", "sync_accounts"): ("src.core.schemas.account.SyncAccountsResponse",),
    (
        "src/core/tools/creative_formats.py",
        "list_creative_formats",
    ): ("src.core.schemas.creative.ListCreativeFormatsResponse",),
    (
        "src/core/tools/media_buy_delivery.py",
        "get_media_buy_delivery",
    ): ("src.core.schemas.delivery.GetMediaBuyDeliveryResponse",),
    ("src/core/tools/media_buy_list.py", "get_media_buys"): ("src.core.schemas._base.GetMediaBuysResponse",),
    ("src/core/tools/products.py", "get_products"): ("src.core.schemas.product.GetProductsResponse",),
    ("src/core/tools/signals.py", "get_signals"): ("src.core.schemas._base.GetSignalsResponse",),
    (
        "src/core/tools/properties.py",
        "list_authorized_properties",
    ): ("src.core.schemas._base.ListAuthorizedPropertiesResponse",),
    ("src/core/tools/creatives/listing.py", "list_creatives"): ("src.core.schemas.creative.ListCreativesResponse",),
    # _update_media_buy_impl returns UpdateMediaBuyResult | UpdateMediaBuySubmitted —
    # BOTH classes can reach this ToolResult(structured_content=...) call.
    ("src/core/tools/media_buy_update.py", "update_media_buy"): (
        "src.core.schemas._base.UpdateMediaBuyResult",
        "src.core.schemas._base.UpdateMediaBuySubmitted",
    ),
    ("src/core/tools/media_buy_create.py", "create_media_buy"): ("src.core.schemas._base.CreateMediaBuyResult",),
    (
        "src/core/tools/capabilities.py",
        "get_adcp_capabilities",
    ): ("adcp.types.GetAdcpCapabilitiesResponse",),
    (
        "src/core/tools/performance.py",
        "update_performance_index",
    ): ("src.core.schemas._base.UpdatePerformanceIndexResponse",),
    ("src/core/tools/signals.py", "activate_signal"): ("src.core.schemas._base.ActivateSignalResponse",),
    ("src/core/tools/creatives/sync_wrappers.py", "sync_creatives"): (
        "src.core.schemas.creative.SyncCreativesResponse",
    ),
}

# Classes that legitimately do NOT carry NestedModelSerializerMixin, with why.
# TaskResultEnvelope family: envelope._serialize calls self.response.model_dump()
# as a real Python method call (not a pydantic_core bypass), correctly reaching
# the class's own exclude_none=True override.
# GetAdcpCapabilitiesResponse: library-native adcp.types class, not ours to fix
# without a local wrapper.
_ALLOWLIST: dict[str, str] = {
    "src.core.schemas._base.UpdateMediaBuyResult": "TaskResultEnvelope family — real .model_dump() call reaches "
    "exclude_none=True (oyiv.7 sweep-verify)",
    "src.core.schemas._base.CreateMediaBuyResult": "TaskResultEnvelope family — same as UpdateMediaBuyResult",
    "adcp.types.GetAdcpCapabilitiesResponse": "library-native adcp.types class, out of local codebase scope",
}


def _all_dotted_classes() -> list[str]:
    return sorted({dotted for classes in _TOOLRESULT_SITES.values() for dotted in classes})


def _resolve_class(dotted: str) -> type:
    module_path, _, class_name = dotted.rpartition(".")
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _carries_mixin(cls: type) -> bool:
    from src.core.schemas._base import NestedModelSerializerMixin

    return issubclass(cls, NestedModelSerializerMixin)


def _find_toolresult_sites(tree: ast.Module) -> list[tuple[str, int]]:
    """Return (enclosing_function_name, lineno) for structured_content= calls."""
    sites: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in iter_call_expressions(node, "ToolResult"):
            if any(kw.arg == "structured_content" for kw in call.keywords):
                sites.append((node.name, call.lineno))
    return sites


def _scan_all_sites() -> list[tuple[str, str, int]]:
    """(repo-relative file, function name, lineno) for every live call site."""
    found: list[tuple[str, str, int]] = []
    for py_file in sorted(_SCAN_DIR.rglob("*.py")):
        tree = safe_parse(py_file)
        if tree is None:
            continue
        rel_path = str(py_file.relative_to(repo_root()))
        for func_name, lineno in _find_toolresult_sites(tree):
            found.append((rel_path, func_name, lineno))
    return found


class TestMcpNullFieldLeakGuard:
    def test_meta_scan_matches_known_site_count(self) -> None:
        """Non-vacuity: the live scan must find exactly the sites in the table.

        A new ToolResult(structured_content=...) construction site (or a
        renamed/removed one) fails here loudly, forcing a deliberate
        MIGRATE/ALLOWLIST decision instead of silently inheriting the bug.
        """
        found = _scan_all_sites()
        found_keys = {(file, func) for file, func, _lineno in found}
        known_keys = set(_TOOLRESULT_SITES)

        new = found_keys - known_keys
        missing = known_keys - found_keys
        assert not new, f"New ToolResult(structured_content=...) site(s) not in _TOOLRESULT_SITES: {sorted(new)}"
        assert not missing, f"_TOOLRESULT_SITES site(s) no longer found by the scan: {sorted(missing)}"

    def test_every_response_class_omits_null_or_is_allowlisted(self) -> None:
        """Every MCP structured_content response class must either carry
        NestedModelSerializerMixin or be on the explicit, reasoned ALLOWLIST.

        A single assert_violations_match_allowlist call covers both directions:
        a class without the mixin and not yet allowlisted is a NEW violation
        (fix it or allowlist it with a reason); an allowlisted class that no
        longer needs the allowlist (it now carries the mixin, or its site was
        removed from _TOOLRESULT_SITES entirely) is a STALE entry to delete.
        """
        found_violations = {dotted for dotted in _all_dotted_classes() if not _carries_mixin(_resolve_class(dotted))}
        assert_violations_match_allowlist(
            {(dotted,) for dotted in found_violations},
            {(dotted,) for dotted in _ALLOWLIST},
            fix_hint="Add NestedModelSerializerMixin as the class's first base, or add/remove a "
            "reasoned _ALLOWLIST entry.",
        )


class TestMcpNullFieldLeakGuardMetaTests:
    """Positive + negative meta-tests for the detector itself (syntactic guard,
    no regex — AST call-site matching is exact, so no regex-slip case applies)."""

    def test_detects_toolresult_structured_content_call(self) -> None:
        source = """
def my_wrapper():
    return ToolResult(content=str(response), structured_content=response)
"""
        tree = ast.parse(source)
        sites = _find_toolresult_sites(tree)
        assert sites == [("my_wrapper", 3)]

    def test_ignores_toolresult_without_structured_content(self) -> None:
        source = """
def my_wrapper():
    return ToolResult(content="just text")
"""
        tree = ast.parse(source)
        assert _find_toolresult_sites(tree) == []

    def test_ignores_non_toolresult_calls(self) -> None:
        source = """
def my_wrapper():
    return SomethingElse(structured_content=response)
"""
        tree = ast.parse(source)
        assert _find_toolresult_sites(tree) == []

    def test_mixin_check_positive_and_negative(self) -> None:
        from src.core.schemas._base import ActivateSignalResponse, NestedModelSerializerMixin

        class NotMixed:
            pass

        assert _carries_mixin(ActivateSignalResponse) is True
        assert issubclass(NestedModelSerializerMixin, object)  # sanity: mixin importable
        assert _carries_mixin(NotMixed) is False
