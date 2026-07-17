"""Guard: harness MCP dispatch must route tool calls through with_error_logging.

A harness env method that invokes a production MCP tool wrapper DIRECTLY
(``update_media_buy(ctx=...)`` / ``create_media_buy(ctx=...)`` via asyncio.run)
bypasses the production boundary decorator that real MCP registration applies
(src/core/main.py: ``mcp.tool()(with_error_logging(fn))``). On error the raw
AdCPError propagates instead of an AdCPToolError carrying the two-layer wire
envelope, so McpDispatcher cannot capture ``wire_error_envelope`` — the MCP
error path can't be asserted at the wire layer.

This is invisible to ``make quality`` (no BDD) and was the #1417 bug
(update path) — see also the wider disease class in #1417.

Disease shape (AST-detectable): inside a tests/harness function, a call
``<tool>(ctx=...)`` where ``<tool>`` is a known MCP tool wrapper, in a function
that does NOT reference ``with_error_logging``. The fix wraps first:
``with_error_logging(update_media_buy)`` then calls the wrapped callable — which
has no direct ``<tool>(ctx=...)`` call, so it is compliant.

AST guard → positive + negative meta-tests suffice (no regex-slip case).

Allowlist shrinks as #1417 migrates the remaining sites.

beads: salesagent-ihwl
"""

import ast
from pathlib import Path

from tests.unit._architecture_helpers import assert_violations_match_allowlist, iter_call_expressions

_HARNESS_DIR = Path(__file__).resolve().parents[1] / "harness"

# Production MCP tool wrappers that must be invoked through with_error_logging.
_MCP_TOOLS = {"create_media_buy", "update_media_buy"}

# Known-deferred violations (#1417). (relative_path, enclosing_function).
# Each entry has a FIXME(salesagent-ensj) at the source site. Allowlist only shrinks.
# media_buy_create.py:call_mcp was fixed when the harness MCP path moved to
# _run_mcp_client (no direct create_media_buy(ctx=...) call) — entry removed.
_KNOWN_VIOLATIONS: set[tuple[str, str]] = set()


def _enclosing_funcs_with_direct_tool_call(source: str) -> set[str]:
    """Names of functions that call a known MCP tool directly as ``tool(ctx=...)``."""
    tree = ast.parse(source)
    bad: set[str] = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in iter_call_expressions(func):
            callee = call.func
            if (
                isinstance(callee, ast.Name)
                and callee.id in _MCP_TOOLS
                and any(kw.arg == "ctx" for kw in call.keywords)
            ):
                bad.add(func.name)
                break
    return bad


def _func_references_with_error_logging(source: str, func_name: str) -> bool:
    tree = ast.parse(source)
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) and func.name == func_name:
            for node in ast.walk(func):
                if isinstance(node, ast.Name) and node.id == "with_error_logging":
                    return True
    return False


def _scan_violations() -> set[tuple[str, str]]:
    """Every (file, function) that calls an MCP tool directly without with_error_logging."""
    violations: set[tuple[str, str]] = set()
    for py_file in _HARNESS_DIR.glob("*.py"):
        source = py_file.read_text()
        for func_name in _enclosing_funcs_with_direct_tool_call(source):
            if not _func_references_with_error_logging(source, func_name):
                violations.add((py_file.name, func_name))
    return violations


def test_no_unguarded_direct_mcp_tool_calls():
    """No harness function may call an MCP tool directly without with_error_logging."""
    violations = _scan_violations()
    new = violations - _KNOWN_VIOLATIONS
    assert new == set(), (
        f"New unguarded direct MCP tool call(s) in tests/harness: {sorted(new)}. "
        f"Wrap the tool with with_error_logging(...) before invoking so the MCP error "
        f"path surfaces the wire envelope (salesagent-ihwl). Do NOT add to the allowlist."
    )


def test_known_violations_not_stale():
    """Allowlist only shrinks — a fixed site must be removed from _KNOWN_VIOLATIONS."""
    assert_violations_match_allowlist(
        _scan_violations(),
        _KNOWN_VIOLATIONS,
        fix_hint=(
            "Wrap the tool with with_error_logging(...) before invoking so the MCP error "
            "path surfaces the wire envelope (salesagent-ihwl)."
        ),
    )


def _func_references_name(source: str, func_name: str, name: str) -> bool:
    tree = ast.parse(source)
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) and func.name == func_name:
            for node in ast.walk(func):
                if isinstance(node, ast.Attribute) and node.attr == name:
                    return True
                if isinstance(node, ast.Name) and node.id == name:
                    return True
    return False


def test_migrated_update_site_is_guarded():
    """The #1417 fix: media_buy_dual update MCP path must run the guarded pipeline.

    Since the adcp 6.6 merge the update path mirrors the create path: it routes
    through ``_run_mcp_client`` (the real FastMCP Client pipeline, whose server
    registration applies ``with_error_logging`` in src/core/main.py) instead of
    wrapping the tool inline. Pin that routing plus the absence of a direct
    ``update_media_buy(ctx=...)`` call.
    """
    source = (_HARNESS_DIR / "media_buy_dual.py").read_text()
    tree = ast.parse(source)
    funcs = [
        f
        for f in ast.walk(tree)
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == "_call_update_mcp"
    ]
    assert funcs, "_call_update_mcp disappeared from media_buy_dual.py"
    explicit_wrap = _func_references_with_error_logging(source, "_call_update_mcp")
    real_pipeline = _func_references_name(source, "_call_update_mcp", "_run_mcp_client")
    assert explicit_wrap or real_pipeline, (
        "_call_update_mcp must either route through _run_mcp_client (production-registered "
        "with_error_logging) or wrap the tool with with_error_logging inline (the #1417 fix regressed)."
    )
    # And it must not appear as a direct-call violation.
    assert ("media_buy_dual.py", "_call_update_mcp") not in _scan_violations()


# --- Meta-tests: verify the guard logic itself ---

_BAD_SNIPPET = """
def call_mcp(self, **kwargs):
    tool_result = aio.run(create_media_buy(ctx=mock_ctx, **kwargs))
    return tool_result
"""

_GOOD_SNIPPET = """
def _call_update_mcp(self, **kwargs):
    wrapped = with_error_logging(update_media_buy)
    tool_result = asyncio.run(wrapped(ctx=mock_ctx, **kwargs))
    return tool_result
"""


def test_guard_positive_catches_direct_call():
    """Meta: a direct tool(ctx=...) call with no with_error_logging is flagged."""
    bad = _enclosing_funcs_with_direct_tool_call(_BAD_SNIPPET)
    assert "call_mcp" in bad
    assert not _func_references_with_error_logging(_BAD_SNIPPET, "call_mcp")


def test_guard_negative_accepts_wrapped_call():
    """Meta: wrapping with with_error_logging before calling is compliant."""
    # No direct create/update tool(ctx=...) call — the wrapped callable is invoked instead.
    assert _enclosing_funcs_with_direct_tool_call(_GOOD_SNIPPET) == set()
    assert _func_references_with_error_logging(_GOOD_SNIPPET, "_call_update_mcp")
