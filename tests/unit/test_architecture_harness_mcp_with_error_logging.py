"""Guard: harness MCP dispatch must route tool calls through with_error_logging.

A harness env method that invokes a production MCP tool wrapper DIRECTLY
(``update_media_buy(ctx=...)`` / ``create_media_buy(ctx=...)`` via asyncio.run)
bypasses the production boundary decorator that real MCP registration applies
(src/core/main.py: ``mcp.tool()(with_error_logging(fn))``). On error the raw
AdCPError propagates instead of an AdCPToolError carrying the two-layer wire
envelope, so McpDispatcher cannot capture ``wire_error_envelope`` — the MCP
error path can't be asserted at the wire layer.

This is invisible to ``make quality`` (no BDD) and was the salesagent-ihwl bug
(update path) — see also the wider disease class in salesagent-ensj.

Disease shape (AST-detectable): inside a tests/harness function, a call
``<tool>(ctx=...)`` where ``<tool>`` is a known MCP tool wrapper, in a function
that does NOT reference ``with_error_logging``. The fix wraps first:
``with_error_logging(update_media_buy)`` then calls the wrapped callable — which
has no direct ``<tool>(ctx=...)`` call, so it is compliant.

AST guard → positive + negative meta-tests suffice (no regex-slip case).

Allowlist shrinks as salesagent-ensj migrates the remaining sites.

beads: salesagent-ihwl
"""

import ast
from pathlib import Path

_HARNESS_DIR = Path(__file__).resolve().parents[1] / "harness"

# Production MCP tool wrappers that must be invoked through with_error_logging.
_MCP_TOOLS = {"create_media_buy", "update_media_buy"}

# Known-deferred violations (salesagent-ensj). (relative_path, enclosing_function).
# Each entry has a FIXME(salesagent-ensj) at the source site. Allowlist only shrinks.
_KNOWN_VIOLATIONS: set[tuple[str, str]] = {
    ("media_buy_create.py", "call_mcp"),  # FIXME(salesagent-ensj): wrap create_media_buy with with_error_logging
}


def _enclosing_funcs_with_direct_tool_call(source: str) -> set[str]:
    """Names of functions that call a known MCP tool directly as ``tool(ctx=...)``."""
    tree = ast.parse(source)
    bad: set[str] = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(func):
            if not isinstance(call, ast.Call):
                continue
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
    violations = _scan_violations()
    stale = _KNOWN_VIOLATIONS - violations
    assert stale == set(), (
        f"Allowlisted site(s) no longer violate and must be removed from _KNOWN_VIOLATIONS: {sorted(stale)}"
    )


def test_migrated_update_site_is_guarded():
    """The salesagent-ihwl fix: media_buy_dual update MCP path must use with_error_logging."""
    source = (_HARNESS_DIR / "media_buy_dual.py").read_text()
    assert _func_references_with_error_logging(source, "_call_update_mcp"), (
        "_call_update_mcp must reference with_error_logging (the ihwl fix regressed)."
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
