"""Guard: MCP ToolResult(structured_content=...) must be pre-serialized.

FastMCP's ``ToolResult.__init__`` (``fastmcp/tools/base.py``) serializes a
non-dict ``structured_content`` via ``pydantic_core.to_jsonable_python``, which
BYPASSES any ``model_dump()`` override — including the ``exclude_none=True``
default set by ``AdCPBaseModel`` / ``SalesAgentBaseModel``
(``src/core/schemas/_base.py``). Passing a raw Pydantic response model
straight into ``ToolResult(structured_content=response)`` wire-serializes
unset/None fields as JSON ``null`` instead of omitting them, violating AdCP
3.1.1's absent-means-absent contract (salesagent-rrz8).

The fix is ``src/core/tools/_mcp_boundary.build_tool_result()``, which pre-
serializes via ``response.model_dump(mode="json")`` before constructing
``ToolResult`` — matching what A2A (``_serialize_for_a2a``) and REST
(``api_v1.py``) already do. This guard scans every ``ToolResult(...)`` call
site in ``src/core/tools/`` and fails if ``structured_content=`` is passed a
bare variable/attribute (not a ``.model_dump(...)`` call, and not the
``build_tool_result`` helper itself) — so a future call site can't silently
reintroduce the bypass.

beads: salesagent-rrz8
"""

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
    iter_call_expressions,
)

TOOLS_DIR = Path(__file__).resolve().parents[2] / "src" / "core" / "tools"

# Known violations — allowlist shrinks as violations are fixed. Empty: all 15
# structured_content= call sites now go through build_tool_result() or call
# .model_dump(...) directly (products.py migrated too, for DRY).
KNOWN_VIOLATIONS: set[tuple[str, int]] = set()


def _is_safe_structured_content(value: ast.expr) -> bool:
    """True if the structured_content= value is provably pre-serialized.

    Safe forms:
    - ``<something>.model_dump(...)`` — a direct call to model_dump.
    - A dict literal — hand-built, so field presence is already explicit
      (no None-vs-absent ambiguity to bypass).
    - Anything else is NOT safe: a bare Name/Attribute (the raw model) bypasses
      model_dump's exclude_none override inside FastMCP's ToolResult.
    """
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
        return value.func.attr in {"model_dump", "model_dump_internal"}
    return isinstance(value, ast.Dict)


def _find_violations_in_tree(tree: ast.Module) -> list[int]:
    """Return line numbers of ToolResult(structured_content=<raw>) calls in a parsed tree."""
    lines: list[int] = []
    for node in iter_call_expressions(tree, name="ToolResult"):
        for kw in node.keywords:
            if kw.arg != "structured_content":
                continue
            if _is_safe_structured_content(kw.value):
                continue
            lines.append(node.lineno)
    return lines


def _find_raw_structured_content() -> list[tuple[str, int, str]]:
    """Find ToolResult(structured_content=<raw>) call sites across src/core/tools/.

    Returns list of (relative_path, lineno, source_snippet).
    """
    violations = []

    for py_file in TOOLS_DIR.rglob("*.py"):
        if py_file.name == "_mcp_boundary.py":
            # The helper itself legitimately constructs ToolResult with a
            # pre-serialized dict — it's the fix, not a call site to police.
            continue

        source = py_file.read_text()
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        for node in iter_call_expressions(tree, name="ToolResult"):
            for kw in node.keywords:
                if kw.arg != "structured_content":
                    continue
                if _is_safe_structured_content(kw.value):
                    continue
                rel_path = str(py_file.relative_to(TOOLS_DIR))
                snippet = ast.unparse(kw.value)
                violations.append((rel_path, node.lineno, snippet))

    return violations


class TestMcpStructuredContentSerialization:
    """ToolResult(structured_content=...) must be pre-serialized via model_dump()."""

    @pytest.mark.arch_guard
    def test_no_new_raw_structured_content_violations(self):
        """No NEW ToolResult(structured_content=<raw model>) call sites."""
        all_violations = _find_raw_structured_content()

        new_violations = []
        for rel_path, lineno, snippet in all_violations:
            if (rel_path, lineno) not in KNOWN_VIOLATIONS:
                new_violations.append(f"  {rel_path}:{lineno} — structured_content={snippet}")

        assert not new_violations, (
            f"Found {len(new_violations)} NEW ToolResult(structured_content=<raw model>) call(s).\n"
            "FastMCP's ToolResult bypasses model_dump(exclude_none=True) for non-dict "
            "structured_content, wire-serializing unset fields as JSON null instead of "
            "omitting them (AdCP 3.1.1 absent-means-absent violation, salesagent-rrz8).\n"
            "Use src.core.tools._mcp_boundary.build_tool_result(content, response) instead, "
            "or pass response.model_dump(mode='json') directly.\n" + "\n".join(new_violations)
        )

    @pytest.mark.arch_guard
    def test_known_violations_not_stale(self):
        """Every entry in KNOWN_VIOLATIONS must still exist in the source."""
        all_violations = _find_raw_structured_content()
        actual_sites = {(v[0], v[1]) for v in all_violations}
        assert_violations_match_allowlist(
            actual_sites,
            KNOWN_VIOLATIONS,
            fix_hint="Remove fixed entries from KNOWN_VIOLATIONS.",
        )

    def test_detector_catches_known_bad_snippets(self):
        """Positive meta-test: the detector must flag every known-bad shape."""
        assert_detector_catches_ast_snippets(
            _find_violations_in_tree,
            snippets={
                "bare_name": "ToolResult(content=str(response), structured_content=response)",
                "attribute_access": "ToolResult(content=str(r.data), structured_content=r.data)",
                "wrong_method": ("ToolResult(content=str(response), structured_content=response.model_dump_json())"),
            },
        )

    def test_detector_ignores_safe_snippets(self):
        """Negative meta-test: the detector must NOT flag correctly pre-serialized calls."""
        safe_snippets = {
            "model_dump_call": 'ToolResult(content=str(response), structured_content=response.model_dump(mode="json"))',
            "model_dump_internal_call": "ToolResult(content=str(response), structured_content=response.model_dump_internal())",
            "dict_literal": 'ToolResult(content="ok", structured_content={"formats": []})',
            "unrelated_call": "SomeOtherThing(structured_content=response)",
        }
        for label, source in safe_snippets.items():
            tree = ast.parse(source, filename=f"<known-safe:{label}>")
            assert not _find_violations_in_tree(tree), f"False positive on known-safe snippet: {label}"
